FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt tzdata
COPY app.py .
COPY static ./static
ENV DB_PATH=/data/growlog.db
VOLUME /data
EXPOSE 8087
CMD ["gunicorn", "-b", "0.0.0.0:8087", "-w", "1", "--threads", "4", "app:app"]
