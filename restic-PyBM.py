#!/usr/bin/env python3

# restic wrapper and status checker
# Written by Eric Viseur <eric.viseur@gmail.com>, 2021
# Released under MIT license

# v0.1 - 04/02/21 - Initial release
# v0.2 - 

# ---- imports ----------------------------------------------------------------

import sys
import subprocess
import errno
import yaml
import os
import json
from datetime import datetime,timedelta
from argparse import ArgumentParser

# ---- constants --------------------------------------------------------------

APPDESC = 'A restic wrapper and Nagios-compliant status checker using a YAML configuration file.  Version 0.2.'
CONFIG_FILE = 'backup.yml'

# ---- create the command line options -----------------------------------------

def create_args():
  parser = ArgumentParser(description = APPDESC)

  parser.add_argument('action', action = 'store',
    choices=['run', 'create', 'list', 'prune', 'check'],
    help = 'Action to execute.')

  parser.add_argument('repo', action = 'store',
    help = 'Repository name, as declared in the configuration file.')

  parser.add_argument("-c", "--config-file", action = "store",
    dest = 'configFile', default = CONFIG_FILE,
    help = ("Configuration file location. Default [%s]" % CONFIG_FILE))

  parser.add_argument("--full", action = 'store_true',
    help = 'check action:  Verifies the actual snapshots content on top of repository metadata.')

  parser.add_argument("--age", action = 'store_true',
    help = 'check action:  Verify the age of the snapshots.')

  parser.add_argument("--perfdata", action = "store_true",
    help = 'check action: Outputs Nagios-compliant perfdata metrics')

  parser.add_argument("-v", "--verbose", action = 'store_true',
    help = 'Provide restic output even for successful execution of actions.')

  parser.add_argument("-q", "--quiet", action = 'store_true',
    help = 'Output only error messages.')

  args = parser.parse_args()
  return args

# ---- parse the YAML configuration file --------------------------------------

def parse_config(configFile):

  # Check if the config file exists
  if os.path.exists(configFile):
    # Attempt to read the config file contents
    try:
      stream = open(configFile, 'r')
      dictionary = yaml.load(stream, Loader=yaml.BaseLoader)
      for key, value in dictionary.items():
        if key == 'restic_binary_location':
          resticLocation = value
        elif key == 'repos':
          repos = value
        else:
          print("CRITICAL - Unexpected key in configuration file %s" % configFile)
          exit(2)
      return [resticLocation, repos]
    except:
      print("CRITICAL - Error reading the configuration file %s" % configFile)
      exit(2)
  else:
    print("CRITICAL - Configuration file %s does not exist" % configFile)
    exit(2)


# ---- run a command and return its output
def run_command(command, commandEnv):
  result = subprocess.run(command, env=commandEnv, shell=True, text=True, capture_output=True)
  return result


# ---- generate the output and ensure the repo is unlocked --------------------
def end_script(returnCode, stdOut, stdErr, successMsg, errorMsg, commandEnv, repoLocation, quiet, verbose):
  # Ensure the repository is unlocked
  command = resticLocation + ' unlock --repo ' + repoLocation
  resultUnlock = run_command(command, commandEnv)

  # Process the output
  if not returnCode == 0:
    print("CRITICAL - %s" % errorMsg)
    print("restic output: %s" % stdErr)
    print("unlock output:")
    print(resultUnlock.stdout)
    print(resultUnlock.stderr)
    exit(2)
  else:
    if not resultUnlock.returncode == 0:
      if not quiet:  print("WARNING - Could not unlock %s" % repoLocation)
      if verbose: print("restic output: %s" % stdOut)
      print("unlock output:")
      print(resultUnlock.stdout)
      print(resultUnlock.stderr)      
      exit(1)
    else:
      if not quiet:  print("OK - %s" % successMsg)
      if verbose:
        print("------------------------------------------------------------------------------")
        print(stdOut)
      exit(0)


# ---- mainline ---------------------------------------------------------------
# -----------------------------------------------------------------------------

# Parse the arguments and read the configuration file
args = create_args()
(resticLocation, repos) = parse_config(args.configFile)

# Check if the provided repo exists in the configuration file
if not args.repo in repos.keys():
  print("Repository %s absent from %s" % (args.repo, args.configFile))
  exit(2)

# Prepare an ephemeral environment dictionnary for the restic invocation
commandEnv = os.environ.copy()
commandEnv["RESTIC_PASSWORD"] = repos[args.repo]['key']

# Run the requested action
if args.action == 'create':
  # Create a new restic repo with the infos provided in backup.yml
  command = resticLocation + ' init --repo ' + repos[args.repo]['location']
  result = run_command(command, commandEnv)
  # Return the results
  end_script(
    result.returncode,
    result.stdout,
    result.stderr,
    ("Repository %s successfully created at location %s" % (args.repo, repos[args.repo]['location'])),
    ("Error creating repository %s" % repos[args.repo]['location']),
    commandEnv,
    repos[args.repo]['location'],
    args.quiet,
    args.verbose
  )

if args.action == 'prune':
  # Clean up repo according to provided preservation policy
  command = resticLocation + ' forget --group-by host --keep-within ' + repos[args.repo]['max_age'] + ' --prune --repo ' + repos[args.repo]['location']
  result = run_command(command, commandEnv)
  # Return the results
  end_script(
    result.returncode,
    result.stdout,
    result.stderr,
    ("Repository %s clean up successful" % args.repo),
    ("Error cleaning up repository %s" % args.repo),
    commandEnv,
    repos[args.repo]['location'],
    args.quiet,
    args.verbose
  )

elif args.action == 'check':
  # Check the repository integrity
  command = resticLocation + ' check --repo ' + repos[args.repo]['location']
  if args.full: command = command + ' --read-data'
  result = run_command(command, commandEnv)
  # Check the restic return code
  if not result.returncode == 0:
    errorMessage = ("Error checking repository %s" % args.repo)
  else:
    # If requested, check the snapshots age
    if args.age:
      command = resticLocation + ' snapshots --json --group-by host --repo ' + repos[args.repo]['location']
      result2 = run_command(command, commandEnv)
      if not result2.returncode == 0:
        errorMessage = ("Error getting snapshots for repository %s" % args.repo)
        result.stderr = result.stderr + "\n" + result2.stderr
        result.returncode = 2
      else:
        snaps = json.loads(result2.stdout)
	      # Oldest snapshot is the first one
        oldestTime = snaps[0]['snapshots'][0]['time']
        # Newest snapshot is the last one
        newestTime = snaps[0]['snapshots'][len(snaps[0]['snapshots'])-1]['time']
	      # Convert to Pythonic time structures
        timeFormat = '%Y-%m-%dT%H:%M:%S'
        oldestTime = datetime.strptime(oldestTime[:-16], timeFormat)
        newestTime = datetime.strptime(newestTime[:-16], timeFormat)
        # Compute snapshots ages versus the current time
        currentTime = datetime.now()
        oldDiff = currentTime - oldestTime
        newDiff = currentTime - newestTime
        # Check ages versus config
        if oldDiff > timedelta(days=int(repos[args.repo]['max_age'])):
          errorMessage = ("Oldest snapshot on %s is %s old" % (args.repo, oldDiff))
        if newDiff > timedelta(days=int(repos[args.repo]['min_age'])):
          errorMessage = ("Newest snapshot on %s is %s old" % (args.repo, newDiff))
        else:
          result.stdout = result.stdout + "\n" + ("Newest snapshot age: %s" % newDiff) + "\n" + ("Oldest snapshot age: %s" % oldDiff)
  # Return the results
  end_script(
    result.returncode,
    result.stdout,
    result.stderr,
    ("Repository %s is healthy" % args.repo),
    errorMessage),
    commandEnv,
    repos[args.repo]['location'],
    args.quiet,
    args.verbose
  )

elif args.action == 'list':
  # List snapshots in the repo
  command = resticLocation + ' snapshots --group-by host --repo ' + repos[args.repo]['location']
  result = run_command(command, commandEnv)
  # Return the results
  end_script(
    result.returncode,
    result.stdout,
    result.stderr,
    ("Snapshot list retreived for repository %s" % args.repo),
    ("Error listing snapshots on repository %s" % repos[args.repo]['location']),
    commandEnv,
    repos[args.repo]['location'],
    args.quiet,
    args.verbose
  )

else:
  # Create a new snapshot
  command = resticLocation + ' backup --exclude \'lost+found\' --repo ' + repos[args.repo]['location']
  for folder in repos[args.repo]['includes']:
    command = command + ' ' + folder
  result = subprocess.run(command, env=commandEnv, shell=True, text=True, capture_output=True)
  # Return the results
  end_script(
    result.returncode,
    result.stdout,
    result.stderr,
    ("Snapshot successfully created on repository %s" % args.repo),
    ("Error creating new snapshot on repository %s" % repos[args.repo]['location']),
    commandEnv,
    repos[args.repo]['location'],
    args.quiet,
    args.verbose
  )
