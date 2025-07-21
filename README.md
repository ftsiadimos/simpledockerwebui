By leveraging a user-friendly web interface, you can quickly monitor your Docker containers. This feature provides a streamlined experience for managing applications and services on your infrastructure.

Basic Usage Instructions pulling the imgage from docker hub.

1. Install the docker server if you haven't already.

2. Pull the lightdockerwebui image using the following command:
```
$ docker pull ftsiadimos/lightdockerwebui:version1.0 
```
3. Use the image by running the following command:
```
$ docker run -d -p 8008:8008 lightdockerwebui
```
4. Open URL in a web browser.

<img src="mis/image1.png" width="800" />

To set up the FQDN and port of your local Docker server, please ensure that your Docker daemon is running on a machine where your container(s) will be located

<img src="mis/image2.png" width="800" />
