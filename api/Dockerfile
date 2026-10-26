FROM python:3.12-slim

WORKDIR /app

# Install the libraries first (this layer is cached if requirements don't change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Then copy the code
COPY . .

EXPOSE 5000

# Start with gunicorn: 2 workers, request log printed to the screen
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--access-logfile", "-", "app:app"]