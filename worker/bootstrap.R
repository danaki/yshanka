local({
  r <- getOption("repos")
  r["CRAN"] <- "http://cran.us.r-project.org"
  options(repos = r)
})

source("/root/deps.R")
load("/root/env.RData")
