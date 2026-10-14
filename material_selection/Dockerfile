FROM python:3.10

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    PORT=1105

COPY requirements.minimal.txt ./
RUN pip install --no-cache-dir -r requirements.minimal.txt

COPY . ./
RUN useradd --system --create-home material && \
    mkdir -p /app/results/mature_material /tmp/matplotlib && \
    chown -R material:material /app /tmp/matplotlib

USER material

EXPOSE 1105

CMD ["python", "main.py"]
