"""
Production workflow for AI Cinema Studio Engine (26-Step Production SOP).
Durable orchestration with 3 Human-in-the-Loop phase gates.
"""

from datetime import timedelta
from typing import Any, Dict, List, Optional

try:
    from temporalio import workflow
    from temporalio.common import RetryPolicy
except ImportError:
    class WorkflowShim:
        @staticmethod
        def defn(cls):
            return cls
        @staticmethod
        def run(func):
            return func
        @staticmethod
        def signal(func):
            return func
        @staticmethod
        def query(func):
            return func
        @staticmethod
        async def execute_activity(activity, *args, **kwargs):
            return await activity(*args)
        @staticmethod
        async def wait_condition(condition):
            while not condition():
                pass
    workflow = WorkflowShim()
    RetryPolicy = None

from cinema_engine.workflows.activities import (
    apply_lipsync_activity,
    color_grade_activity,
    encode_platforms_activity,
    generate_audio_activity,
    generate_video_shot_activity,
    parse_brief_activity,
    query_rag_presets_activity,
    remotion_render_activity,
)


@workflow.defn
class ProductionWorkflow:
    """
    Master Production SOP Workflow.
    Coordinates Pre-production -> Production -> Post-production across 26 SOP steps.
    """

    def __init__(self):
        self.phase: str = "init"
        self.current_step: int = 0
        self.gate_1_approved: bool = False
        self.gate_2_approved: bool = False
        self.gate_3_approved: bool = False
        self.rejection_reason: Optional[str] = None
        self.shots_state: List[Dict[str, Any]] = []
        self.audio_state: Optional[Dict[str, Any]] = None
        self.master_video_path: Optional[str] = None
        self.platform_deliverables: Dict[str, str] = {}
        self.error: Optional[str] = None

    @workflow.signal
    def approve_gate(self, gate_number: int) -> None:
        """Human-in-the-loop signal approving a specific phase gate."""
        if gate_number == 1:
            self.gate_1_approved = True
        elif gate_number == 2:
            self.gate_2_approved = True
        elif gate_number == 3:
            self.gate_3_approved = True

    @workflow.signal
    def reject_gate(self, gate_number: int, reason: str) -> None:
        """Human-in-the-loop signal rejecting a phase gate with feedback."""
        self.rejection_reason = f"Gate {gate_number} rejected: {reason}"
        self.error = self.rejection_reason

    @workflow.query
    def get_production_status(self) -> Dict[str, Any]:
        """Query the live state, current phase, and gate approvals."""
        return {
            "phase": self.phase,
            "current_step": self.current_step,
            "gate_1_approved": self.gate_1_approved,
            "gate_2_approved": self.gate_2_approved,
            "gate_3_approved": self.gate_3_approved,
            "shots_count": len(self.shots_state),
            "master_video": self.master_video_path,
            "deliverables": self.platform_deliverables,
            "error": self.error,
        }

    @workflow.run
    async def run(self, brief: Dict[str, Any]) -> Dict[str, Any]:
        """Execute end-to-end cinematic production."""
        activity_args = {}
        if RetryPolicy:
            activity_args = {
                "start_to_close_timeout": timedelta(minutes=10),
                "retry_policy": RetryPolicy(
                    initial_interval=timedelta(seconds=2),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=30),
                    maximum_attempts=3,
                ),
            }

        # =========================================================================
        # PHASE 1: PRE-PRODUCTION (Steps 1-13)
        # =========================================================================
        self.phase = "pre_production"
        self.current_step = 1

        # Step 1-3: Intake brief & initialize shot list
        project_order = await workflow.execute_activity(
            parse_brief_activity,
            brief,
            **activity_args,
        )

        # Step 5-8: Cinematography RAG preset query for each shot
        assembled_shots = []
        for shot in project_order["shots"]:
            self.current_step = 7
            shot_with_rag = await workflow.execute_activity(
                query_rag_presets_activity,
                shot,
                **activity_args,
            )
            assembled_shots.append(shot_with_rag)

        self.shots_state = assembled_shots
        self.current_step = 13  # Phase Gate 1

        # 🚦 PHASE GATE 1: Wait for human sign-off on script, shotlist & prompts
        await workflow.wait_condition(lambda: self.gate_1_approved or self.rejection_reason is not None)
        if self.rejection_reason:
            return {"status": "rejected", "phase": "gate_1", "reason": self.rejection_reason}

        # =========================================================================
        # PHASE 2: PRODUCTION (Steps 14-19)
        # =========================================================================
        self.phase = "production"
        rendered_shots = []

        # Step 15: Video generation across shots
        for idx, shot in enumerate(self.shots_state):
            self.current_step = 15
            shot_job = await workflow.execute_activity(
                generate_video_shot_activity,
                shot,
                **activity_args,
            )
            shot_copy = dict(shot)
            shot_copy["job"] = shot_job
            shot_copy["local_path"] = shot_job.get("local_path")
            rendered_shots.append(shot_copy)

        self.shots_state = rendered_shots

        # Step 17: Voiceover & Audio Generation
        self.current_step = 17
        script_text = project_order.get("script", "")
        if script_text:
            audio_job = await workflow.execute_activity(
                generate_audio_activity,
                script_text,
                brief.get("voice_id"),
                **activity_args,
            )
            self.audio_state = audio_job

        # Step 18: Lip-sync for dialogue shots if requested
        for shot in self.shots_state:
            if shot.get("needs_lipsync") and shot.get("local_path") and self.audio_state and self.audio_state.get("local_path"):
                self.current_step = 18
                ls_job = await workflow.execute_activity(
                    apply_lipsync_activity,
                    shot["local_path"],
                    self.audio_state["local_path"],
                    **activity_args,
                )
                if ls_job.get("local_path"):
                    shot["local_path"] = ls_job["local_path"]

        self.current_step = 19  # Phase Gate 2

        # 🚦 PHASE GATE 2: Wait for human sign-off on raw rendered footage & audio
        await workflow.wait_condition(lambda: self.gate_2_approved or self.rejection_reason is not None)
        if self.rejection_reason:
            return {"status": "rejected", "phase": "gate_2", "reason": self.rejection_reason}

        # =========================================================================
        # PHASE 3: POST-PRODUCTION (Steps 20-26)
        # =========================================================================
        self.phase = "post_production"

        # Step 21: Color grade individual shots
        self.current_step = 21
        for shot in self.shots_state:
            if shot.get("local_path"):
                graded_path = await workflow.execute_activity(
                    color_grade_activity,
                    shot["local_path"],
                    shot.get("grading"),
                    **activity_args,
                )
                shot["graded_path"] = graded_path

        # Step 24: Remotion compositing (assemble shots, title card, branding, audio)
        self.current_step = 24
        clips_props = [
            {"videoUrl": s.get("graded_path") or s.get("local_path"), "duration": s.get("duration_sec", 5)}
            for s in self.shots_state if (s.get("graded_path") or s.get("local_path"))
        ]
        audio_path = self.audio_state.get("local_path") if self.audio_state else None

        remotion_props = {
            "composition_id": brief.get("template", "Generic"),
            "clips": clips_props,
            "audio_url": audio_path,
            "title": project_order.get("project_name", "AI Cinema"),
            "description": brief.get("client", ""),
        }
        master_output = await workflow.execute_activity(
            remotion_render_activity,
            remotion_props,
            **activity_args,
        )
        self.master_video_path = master_output

        # Step 25: Multi-Platform packaging (YouTube, Shorts/Reels/TikTok, Square)
        self.current_step = 25
        platform_files = await workflow.execute_activity(
            encode_platforms_activity,
            master_output,
            **activity_args,
        )
        self.platform_deliverables = platform_files

        # Step 26: 🚦 PHASE GATE 3: Final client & QA review before distribution
        self.current_step = 26
        await workflow.wait_condition(lambda: self.gate_3_approved or self.rejection_reason is not None)
        if self.rejection_reason:
            return {"status": "rejected", "phase": "gate_3", "reason": self.rejection_reason}

        self.phase = "completed"
        return {
            "status": "published",
            "master_video": self.master_video_path,
            "deliverables": self.platform_deliverables,
            "shots": self.shots_state,
        }
