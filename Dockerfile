FROM python:3
WORKDIR /usr/src/app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && apt update && apt install ffmpeg youtube-dl -y --no-install-recommends
COPY . .
CMD ["python", "./main.py"]
EXPOSE 5151