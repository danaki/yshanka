import docker

docker_client = docker.Client(**docker.utils.kwargs_from_env(assert_hostname=False))
