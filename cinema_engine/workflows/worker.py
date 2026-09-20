"""
Temporal Worker for AI Cinema Studio Engine.
Polls task queues and executes activities and production workflows.
"""

import asyncio
from temporalio.client import Client
from temporalio.worker import Worker

from cinema_engine.config import get_settings
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
from cinema_engine.workflows.production import ProductionWorkflow


TASK_QUEUE = "ai-studio-queue"


async def run_worker():
    settings = get_settings()
    temporal_host = "localhost:7233"  # or from env
    client = await Client.connect(temporal_host)

    activities = [
        parse_brief_activity,
        query_rag_presets_activity,
        generate_video_shot_activity,
        generate_audio_activity,
        apply_lipsync_activity,
        color_grade_activity,
        remotion_render_activity,
        encode_platforms_activity,
    ]

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ProductionWorkflow],
        activities=activities,
    )

    print(f"AI Studio Temporal Worker started on queue: {TASK_QUEUE}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())
