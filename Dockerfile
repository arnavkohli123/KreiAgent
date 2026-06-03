FROM python:3.12-slim

# Install system packages Playwright needs to run Chrome
RUN apt-get update && apt-get install -y \
    wget curl gnupg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python packages first (cached layer — only rebuilds if requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright's Chromium browser + its system dependencies
RUN playwright install chromium --with-deps

# Copy the rest of the code
COPY . .

CMD ["python", "scrape_crexi.py"]
