FROM phusion/baseimage
CMD ["/sbin/my_init"]
RUN apt-get update && apt-get install -y \
   r-cran-rserve \
   && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

RUN mkdir /etc/service/rserve
ADD rserve_run.runit /etc/service/rserve/run
RUN chmod +x /etc/service/rserve/run

RUN mkdir /etc/service/rserve/log
ADD rserve_log_run.runit /etc/service/rserve/log/run
RUN chmod +x /etc/service/rserve/log/run

RUN echo "/usr/lib/R" > /etc/container_environment/R_HOME

ADD Rserve.conf /root
ADD bootstrap.R /root
ADD env.Rdata /root
ADD deps.R /root

EXPOSE 6311
