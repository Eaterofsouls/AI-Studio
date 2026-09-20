FROM python:3.12-slim

# Install system dependencies: FFmpeg with full filters
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency specifications
COPY requirements.txt pyproject.toml ./

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY cinema_engine/ cinema_engine/
COPY luts/ luts/

# Expose port
EXPOSE 8000

# Run FastAPI server
CMD ["uvicorn", "cinema_engine.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
