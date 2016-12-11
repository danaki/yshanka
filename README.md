`Yshanka`: Yhat ScienseOps poorman's drop-in replacement
========================================================

At the moment:

* Only R models (via Rserve)
* No multiple workers (local docker)
* No user permissions
* Hackish and dirty code

It's proof of concept. If you want me to continue development please say 'Hello' to pimenoff at gmail or via github messaging.


Installation
----------------

```
$ git clone ... ; cd ...
$ conda env create -n yshanka
$ python manage.py db upgrade
$ python manage.py seed
```

Run
----------------

```
$ docker-machine start
$ python manage.py run
```

* Go to http://localhost:5000/admin/login/
* Login: admin@example.com
* Password: admin



```r
install.packages('yhatr')
library(yhatr)

yhat.config  <- c(
  username="admin",
  apikey="adminadminadmin",
  env="http://localhost:5000"
)

model.predict <- function(request) {
  me <- request$name
  greeting <- paste ("Hello", me, "!")
  greeting
}

yhat.deploy("HelloWorld", confirm = FALSE)
yhat.predict("HelloWorld", data.frame(name="yshanka!"))
```


