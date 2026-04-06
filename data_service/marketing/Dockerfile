FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy script
COPY generate_social_content.py .

# Output directory — mount a host volume here to persist generated drafts
RUN mkdir -p output

ENTRYPOINT ["python", "generate_social_content.py"]

# Default: generate all content types.
# Override at runtime:  docker-compose run --rm marketing reddit
CMD ["all"]
