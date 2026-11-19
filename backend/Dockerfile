FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN ffmpeg -version >/dev/null \
    && streamlink --version >/dev/null \
    && python -c "import cv2, mediapipe as mp, pytchat, streamlink; detector = mp.solutions.face_detection.FaceDetection(model_selection=0, min_detection_confidence=0.5); detector.close(); print('FFmpeg, Streamlink, pytchat and MediaPipe smoke check passed:', cv2.__version__, mp.__version__)"
COPY app ./app

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
