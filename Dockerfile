FROM rockylinux:9
COPY . /app
WORKDIR /app
RUN dnf install pip -y
RUN pip install -r requirements.txt
#CMD ["flask", "run"]