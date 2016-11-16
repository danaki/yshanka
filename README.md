Yshanka: Yhat ScienseOps poorman's drop-in replacement.

== Installation ==

```
$ git clone ... ; cd ...
$ conda env create -n yshanka
$ python manage.py db upgrade
$ python manage.py seed
```

== Run ===
```
$ R_HOME="/usr/lib/R" R_LIB="Rlib" /usr/lib/R/bin/Rserve --RS-conf Rserve.conf --RS-source code.R --vanilla
$ python manage.py runserver
```

To be continued...