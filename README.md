A user-friendly web interface, you can quickly monitor your Docker containers. This feature provides a streamlined experience for managing containers.

Basic Usage Instructions pulling the imgage from docker hub. https://hub.docker.com/r/ftsiadimos/lightdockerwebui

1. Install the docker server if you haven't already.

2. Pull the lightdockerwebui image using the following command:
```
$ docker pull ftsiadimos/lightdockerwebui:version1.0 
```
3. Use the image by running the following command:
```
$ docker run -d -p 8008:8008 ftsiadimos/lightdockerwebui:version1.0 
```
4. Open URL in a web browser.


<img src="mis/image1.png" width="800" style='border:130px solid #555' />

Set up the FQDN or IP and port of your local Docker server, please ensure that your Docker daemon is running on a machine where your container(s) will be located

<img src="mis/image2.png" width="800" />
