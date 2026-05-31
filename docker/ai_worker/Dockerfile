FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System deps for building native tools and ffmpeg usage
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git wget libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy project
COPY . /app

# Install Python deps (adjust as needed)
RUN pip install --upgrade pip
RUN pip install django requests jsonschema
# Optional CV deps for object detection (install when building worker image)
RUN pip install ultralytics opencv-python-headless

# (Optional) Build whisper.cpp here if you plan to use it inside the image.
# We'll leave the build step commented as a reminder.
# RUN git clone https://github.com/ggerganov/whisper.cpp.git /tmp/whisper.cpp \
#     && make -C /tmp/whisper.cpp

COPY docker/ai_worker/worker-entrypoint.sh /app/worker-entrypoint.sh
RUN chmod +x /app/worker-entrypoint.sh

CMD ["/app/worker-entrypoint.sh"]
