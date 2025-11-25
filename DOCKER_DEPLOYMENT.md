# MANIM FastAPI Docker Deployment Guide

## Overview

This Docker setup deploys the MANIM FastAPI service for generating quiz videos with animations.

## Prerequisites

- Docker and Docker Compose installed
- At least 4GB RAM recommended (Manim rendering is memory-intensive)
- Sufficient disk space for temporary video files

## System Dependencies

The Docker image includes:
- **Python 3.11**
- **FFmpeg** - For audio/video processing
- **Sox** - For audio processing
- **ImageMagick** - For image manipulation
- **LaTeX** (TeX Live) - For math rendering (Linux)
- **Manim** - Animation library
- **FastAPI** - Web framework

### LaTeX Configuration

**For Local Development (Windows with MiKTeX):**
- The application automatically detects and uses MiKTeX if installed
- Searches common MiKTeX installation paths:
  - `C:\Program Files\MiKTeX\miktex\bin\x64`
  - `C:\Program Files (x86)\MiKTeX\miktex\bin\x64`
  - User-specific installations in `%LOCALAPPDATA%`
- Falls back to system PATH if MiKTeX is not found
- Check console output for "Using MiKTeX LaTeX compiler" message

**For Docker (Linux):**
- Uses TeX Live (included in Docker image)
- MiKTeX is Windows-specific and not available in Linux containers

## Building and Running

### Using Docker Compose (Recommended)

```bash
# Build and start
docker-compose up -d --build

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

### Using Docker Directly

```bash
# Build
docker build -t manim-fastapi .

# Run
docker run -d \
  -p 8001:8001 \
  -v $(pwd)/temp:/tmp \
  --name manim-fastapi \
  manim-fastapi
```

## API Endpoint

The service runs on port **8001** by default.

### Generate Video

```bash
POST http://localhost:8001/generate-video
Content-Type: multipart/form-data

Form fields:
- question: str (required)
- options: str (required, JSON string: {"A": "...", "B": "..."})
- correct_answer: str (required, e.g., "A")
- explanation: str (required)
- image: file (optional)
- audio: file (optional, explanation audio)
- question_audio: file (optional, question audio)
- subtitles: file (optional, SRT file for explanation)
- question_subtitles: file (optional, SRT file for question)
- font_name: str (optional, default: "Pangolin")
```

### Example Request

```bash
curl -X POST http://localhost:8001/generate-video \
  -F "question=What is 2+2?" \
  -F "options={\"A\": \"3\", \"B\": \"4\", \"C\": \"5\", \"D\": \"6\"}" \
  -F "correct_answer=B" \
  -F "explanation=Two plus two equals four." \
  -F "font_name=Pangolin"
```

## File Structure

```
MANIM/
├── MANIM_FASTAPI.PY    # Main FastAPI application
├── assets/              # Asset files (sounds, SVGs)
│   ├── correct.wav
│   ├── hand-vector.svg
│   ├── penonpaper.mp3
│   ├── roll.mp3
│   ├── swipe.wav
│   └── whoosh.mp3
├── QMR.ttf             # Font file (optional)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Assets

The following assets are used by the application:
- **correct.wav** - Sound played when correct answer is shown
- **hand-vector.svg** - Hand animation for writing effect
- **whoosh.mp3** - Sound for question slide-in
- **swipe.wav** - Sound for options slide-in
- **penonpaper.mp3** - Writing sound effect
- **roll.mp3** - Rolling sound effect

## Fonts

The application supports:
- **Google Fonts** - Automatically downloaded via `manim-fonts`
- **Local fonts** - Place `.ttf` files in the MANIM directory (e.g., `QMR.ttf`)

Default font: **Pangolin**

## Temporary Files

Video rendering creates temporary files in `/tmp` (inside container). These are:
- Automatically cleaned up after video generation
- Stored in `./temp` if volume is mounted (for debugging)

## Performance Considerations

1. **Memory**: Manim rendering is memory-intensive. Ensure Docker has at least 4GB RAM allocated.
2. **CPU**: Video rendering is CPU-intensive. Multiple concurrent requests may slow down.
3. **Disk Space**: Temporary files can be large. Monitor disk usage.
4. **Rendering Time**: Complex animations may take 30-60 seconds to render.

## Troubleshooting

### Container Fails to Start

1. Check logs: `docker-compose logs manim-fastapi`
2. Verify all dependencies are installed
3. Check disk space: `df -h`

### Video Generation Fails

1. Check container logs for errors
2. Verify input files (images, audio) are valid
3. Check LaTeX rendering (math expressions may fail if syntax is incorrect)
4. Verify font is available (Google Fonts or local file)

### Out of Memory

1. Increase Docker memory limit
2. Process requests sequentially (limit concurrent requests)
3. Reduce video quality in Manim config (if needed)

### Font Not Found

1. Check font name spelling
2. Verify Google Fonts connection (for online fonts)
3. Ensure local font file exists and is copied to container

## Health Check

The service includes a health check endpoint:
- **Endpoint**: `/docs` (FastAPI auto-generated docs)
- **Interval**: 30 seconds
- **Timeout**: 10 seconds

## API Documentation

Once running, visit:
- **Swagger UI**: http://localhost:8001/docs
- **ReDoc**: http://localhost:8001/redoc

## Production Considerations

1. **Security**: Add API authentication if needed
2. **Rate Limiting**: Implement rate limiting for video generation
3. **Queue System**: Consider a queue system (Celery, Redis) for high traffic
4. **Caching**: Cache frequently requested videos
5. **Monitoring**: Add monitoring/logging (Prometheus, Grafana)
6. **Resource Limits**: Set CPU/memory limits in docker-compose.yml

## Environment Variables

Currently, no environment variables are required. You can add them for:
- Port configuration
- Font paths
- Asset paths
- Rendering quality settings

