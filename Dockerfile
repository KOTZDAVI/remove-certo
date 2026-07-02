FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/
COPY icon.png .

RUN mkdir -p uploads results originals backgrounds

# Railway injeta PORT em tempo de execução; o app.py já lê essa variável.
CMD ["python", "app.py"]
