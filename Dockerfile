FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OMNIPAY_DB=/data/omnipay.db PORT=8000
RUN useradd -m -u 10001 omni && mkdir /data && chown omni /data
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY omnipay.py promo.py ./
COPY app ./app
COPY migrations ./migrations
COPY web ./web
USER omni
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"
# ONE worker on purpose: the rate limiter and the background-jobs thread are in-process (see README 'Scaling').
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:8000", "--access-logfile", "-", "app.serve:application()"]
