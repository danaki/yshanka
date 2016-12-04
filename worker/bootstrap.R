local({
  r <- getOption("repos")
  r["CRAN"] <- "http://cran.us.r-project.org"
  options(repos = r)
})

load("/root/env.RData")
source("/root/deps.R")
