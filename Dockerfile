# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies for Manim
# Manim requires: ffmpeg, sox, ImageMagick, LaTeX, and various libraries
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    ffmpeg \
    sox \
    imagemagick \
    texlive \
    texlive-latex-extra \
    texlive-fonts-extra \
    texlive-latex-recommended \
    libcairo2-dev \
    libpango1.0-dev \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-xlib-2.0-dev \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install -r requirements.txt

# Copy application code
COPY MANIM_FASTAPI.PY .

# Copy assets directory (if it exists)
COPY assets/ ./assets/

# Copy font file if it exists (QMR.ttf)
COPY QMR.ttf* ./

# Create temp directory for video rendering
RUN mkdir -p /tmp && chmod 777 /tmp

# Create a non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/docs')" || exit 1

# Run the application
CMD ["python", "MANIM_FASTAPI.PY"]

