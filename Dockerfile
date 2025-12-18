FROM python:3.12-slim AS builder
COPY . /app
WORKDIR /app
#RUN dnf install pip -y
RUN pip install -r requirements.txt
CMD ["/usr/local/bin/gunicorn", "-b", ":8008", "start:app"]
#CMD ["flask", "run"]