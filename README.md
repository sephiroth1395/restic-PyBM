# restic-PyBM

A Python 3 wrapper to manage `restic` repositories and monitor them with Nagios-compliant outputs

- [restic-PyBM](#restic-pybm)
  - [Why?](#why)
  - [Features](#features)
  - [Roadmap](#roadmap)
  - [Usage](#usage)
    - [Configuration file](#configuration-file)
    - [Application](#application)

## Why?

When looking for a tool to backup my homelab and personal documents, `restic` came out as the strongest contender for FOSS solutions.

Implementation proved to be easy and simple.

restic-PyBM started out as a Bash script to automate stuff.  Then the need to monitor the backups appeared, then the script started to look horrendous, then I wanted to prepare for integration with one-time repository keys generated via `HashiCorp Vault`.

So I decided to rewrite things in a _somewhat_ cleaner manner in Python, with a YAML configuration file.

## Features

* _Should_ transparently support all repository locations supported by `restic`.
* Already tested with `local` and `REST server` repos.  __Feedback on other types welcome!__
* Repos initialisation
* Execution of backups
* Old snapshots cleanup based on an age policy
* Repository health & age checks with Nagios-compliant outputs

## Roadmap

* Batch execution of a command on all repos
* Repositories deduplication
* Integration with `HashiCorp Vault`
* Option to auto-update `restic` upon invocation
* Repository passwords management (add and delete)
* Support for optional `excludes`.

## Usage

### Configuration file

The script uses a `YAML` configuration file:

```
restic_binary_location: /opt/restic
repos:
  repo1:
    location: /root/test
    key: aaaa
    min_age: 1d
    max_age: 7d
    includes:
      - /tmp
  repo2:
    location: rest:https://rest-server.local:8000/server_babel
    key: bbb
    min_age: 3d
    max_age: 15d
    includes:
      - /etc
      - /usr/local/lib
```

* `restic_binary_location` points to the location of the actual `restic` binary.  The script __does not__ handle the deployment of `restic` itself.
* Inside the `repos` object, each repository is identified by a `label` and contains four fields:
  * `location`: A `restic`-compliant repository address
  * `key`: A password for this repo.
  * `min-age`: The minimal age of the newest snapshot in the repository.  Used for `check`.
  * `max-age`: The maximum age of the oldest snapshot in the repository.  Used for `check` and `prune`.
  * `includes`: A list of folder and/or files to backup in the snapshots.

### Application

```
usage: restic-PyBM.py [-h] [-c CONFIGFILE] [--full] [--perfdata] [-v] [-q]
                      {run,create,list,prune,check} repo

A restic wrapper and Nagios-compliant status checker using a YAML
configuration file. Version 0.1.

positional arguments:
  {run,create,list,prune,check}
                        Action to execute.
  repo                  Repository name, as declared in the configuration
                        file.

optional arguments:
  -h, --help            show this help message and exit
  -c CONFIGFILE, --config-file CONFIGFILE
                        Configuration file location. Default [backup.yml]
  --full                check action: Verifies the actual snapshots content on
                        top of repository metadata.
  --perfdata            check action: Outputs Nagios-compliant perfdata
                        metrics
  -v, --verbose         Provide restic output even for successful execution of
                        actions.
  -q, --quiet           Output only error messages.
```