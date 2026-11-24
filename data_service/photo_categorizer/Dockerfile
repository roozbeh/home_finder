FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_PROGRESS_BAR=off

COPY photo_categorizer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY photo_categorizer/categorize_photos.py photo_categorizer/active_listing.py \
     photo_categorizer/photo_inference.py photo_categorizer/bulk_moondream.py \
     photo_categorizer/gallery_photo_labels.py photo_categorizer/import_gallery_photo_labels.py \
     flip_ui/buyer_media.py flip_ui/buyer_photo_labels.py ./
ENTRYPOINT ["python", "/app/categorize_photos.py"]
