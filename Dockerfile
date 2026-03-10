FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

EXPOSE 8000

# Bind to 0.0.0.0 so the server is reachable from outside the container
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--workers", "2"]
