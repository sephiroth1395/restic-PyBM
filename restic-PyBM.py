#!/usr/bin/env python3

# restic wrapper and status checker
# Written by Eric Viseur <eric.viseur@gmail.com>, 2021
# Released under MIT license

# v0.1 - 04/02/21 - Initial release
# v0.2 - In progress - Minor fixes, restic auto-update

# ---- imports ----------------------------------------------------------------

import sys
import subprocess
import errno
import yaml
import os
import json
from datetime import datetime, timedelta
from argparse import ArgumentParser

# ---- constants --------------------------------------------------------------

APPDESC = 'A restic wrapper and Nagios-compliant status checker using a YAML configuration file.  Version 0.2.'
CONFIG_FILE = 'backup.yml'

# ---- create the command line options -----------------------------------------


def create_args():
    parser = ArgumentParser(description=APPDESC)

    parser.add_argument('action', action='store',
                        choices=['run', 'create', 'list', 'prune', 'check'],
                        help='Action to execute.')

    parser.add_argument('repo', action='store', nargs='?', default='ALL_REPOS',
                        help='Repository name, as declared in the configuration file. If omitted, the action is executed on all repos.')

    parser.add_argument("-c", "--config-file", action="store",
                        dest='configFile', default=CONFIG_FILE,
                        help=("Configuration file location. Default [%s]" % CONFIG_FILE))

    parser.add_argument("--full", action='store_true',
                        help='check action:  Verifies the actual snapshots content on top of repository metadata.')

    parser.add_argument("--age", action='store_true',
                        help='check action:  Verify the age of the snapshots.')

    parser.add_argument("--perfdata", action="store_true",
                        help='check action: Outputs Nagios-compliant perfdata metrics')

    parser.add_argument("-v", "--verbose", action='store_true',
                        help='Provide restic output even for successful execution of actions.')

    parser.add_argument("-q", "--quiet", action='store_true',
                        help='Output only error messages.')

    parser.add_argument("-u", "--self-update", action='store_true',
                        dest='selfUpdate', help='Self-update restic before any other action.')

    parser.add_argument("-V", "--use-vault", action='store_true',
                        dest='vault', help='Get the repositories passwords from HashiCorp Vault.')

    args = parser.parse_args()
    return args

# ---- parse the YAML configuration file --------------------------------------


def parse_config(configFile):

  # Check if the config file exists
  if os.path.exists(configFile):
    # Attempt to read the config file contents
    try:
      stream = open(configFile, 'r')
      configValues = yaml.load(stream, Loader=yaml.BaseLoader)

      resticLocation = configValues['restic_binary_location']
      repos = configValues['repos']

      if 'vault' in configValues.keys(): vaultData = configValues['vault']
      else: vaultData = ''

      return [resticLocation, repos, vaultData]
    except:
      print("CRITICAL - Error reading the configuration file %s" %
            configFile)
      exit(2)
  else:
    print("CRITICAL - Configuration file %s does not exist" % configFile)
    exit(2)


# ---- run a command and return its output
def run_command(command, commandEnv):
  result = subprocess.run(command, env=commandEnv,
                          shell=True, text=True, capture_output=True)
  return result

# ---- obtain a repository password -------------------------------------------
def get_repo_password(repos, currentRepo, vault = False):
  complexMethods = ['s3:', 'b2:'];
  if vault:
    vaultRead = vault.secrets.kv.v2.read_secret_version(
      path=repos[currentRepo]['key']['path'], 
      mount_point=repos[currentRepo]['key']['mountpoint']
    )
    if repos[currentRepo]['location'][0:3] in complexMethods:
      return(vaultRead['data']['data'])
    else:
      return(vaultRead['data']['data']['password'])
  else:
    return(repos[currentRepo]['key'])

# ---- generate the output and ensure the repo is unlocked --------------------
def end_script(returnCode, stdOut, stdErr, successMsg, errorMsg, quiet, verbose):

  # Process the output
  if returnCode == 2:
    print("CRITICAL - %s" % errorMsg)
    print("Output: %s" % stdOut)
    print("Error: %s" % stdErr)
    exit(2)
  else:
    if returnCode == 1:
      if not quiet:
        print("WARNING - %s" % errorMsg)
      if verbose:
        print("Output: %s" % stdOut)
        print("Error: %s" % stdErr)
      exit(1)
    else:
      if not quiet:
        print("OK - %s" % successMsg)
      if verbose:
        print("------------------------------------------------------------------------------")
        print(stdOut)
      exit(0)


# ---- mainline ---------------------------------------------------------------
# -----------------------------------------------------------------------------

# Parse the arguments and read the configuration file
args = create_args()
(resticLocation, repos, vaultData) = parse_config(args.configFile)

# Check if the provided repo exists in the configuration file
if not args.repo in repos.keys() and not args.repo == 'ALL_REPOS':
  print("Repository %s absent from %s" % (args.repo, args.configFile))
  exit(2)

# Prepare an ephemeral environment dictionnary for the restic invocation
commandEnv = os.environ.copy()

# If requested, self update restic first
if args.selfUpdate:
    command = resticLocation + ' self-update'
    result = run_command(command, commandEnv)
    if not result.returncode == 0:
        print("CRITICAL - restic self-update failed: %s." % result.stderr)
        exit(2)

# Build a list with the repos to process
reposToProcess = []
if args.repo == 'ALL_REPOS':
  for entry in repos:
    reposToProcess.append(entry)
else:
  reposToProcess.append(args.repo)

# If Vault is to be used, open the connection
if args.vault:
    import hvac
    vault = hvac.Client(url=vaultData['server'])
    vault.auth.approle.login(
      role_id=vaultData['role_id'],
      secret_id=vaultData['secret_id'],
    )

# Initialize accumulation variables used to create the script output messages
successMessageAccumulated = ''
errorMessageAccumulated = ''
stdoutAccumulated = ''
stderrAccumulated = ''
scriptReturnValue = 0

# Run the requested action on all selected repositories
for currentRepo in reposToProcess:

  # Get the repository credentials
  if args.vault: repoCredentials = get_repo_password(repos, currentRepo, vault)
  else: repoCredentials = get_repo_password(repos, currentRepo)  
  
  if repos[currentRepo]['location'][0:3] == 'b2:':
    commandEnv["B2_ACCOUNT_ID"] = repoCredentials['keyID']
    commandEnv["B2_ACCOUNT_KEY"] = repoCredentials['applicationKey']
    commandEnv["RESTIC_PASSWORD"] = repoCredentials['password']
  elif repos[currentRepo]['location'][0:3] == 's3:':
    commandEnv["AWS_ACCESS_KEY_ID"] = repoCredentials['keyID']
    commandEnv["AWS_SECRET_ACCESS_KEY"] = repoCredentials['applicationKey']
    commandEnv["RESTIC_PASSWORD"] = repoCredentials['password']
  else:
    commandEnv["RESTIC_PASSWORD"] = repoCredentials

  # If this a duplicate type repo, also get the source repository key
  if 'duplicate' in repos[currentRepo].keys():
    duplicateSource = repos[currentRepo]['duplicate']

    if args.vault: repoCredentials2 = get_repo_password(repos, duplicateSource, vault)
    else: repoCredentials2 = get_repo_password(repos, duplicateSource)
    commandEnv["RESTIC_PASSWORD2"] = repoCredentials2

    # When duplicating we need to invert the password variables 1 and 2
    if args.action == 'run':
      buffer = commandEnv["RESTIC_PASSWORD2"]
      commandEnv["RESTIC_PASSWORD2"] = commandEnv["RESTIC_PASSWORD"]
      commandEnv["RESTIC_PASSWORD"] = buffer


  
  # ---- actions execution ----------------------------------------------------

  if args.action == 'create':
      # Create a new restic repo with the infos provided in backup.yml
      command = resticLocation + ' init --repo ' + repos[currentRepo]['location']
      # If this is a repo that will hold duplicates  amend the restic command
      if 'duplicate' in repos[currentRepo].keys():
        command += ' --repo2 ' + repos[duplicateSource]['location'] + ' --copy-chunker-params'

      result = run_command(command, commandEnv)
      # Return the results
      successMessage = ("Repository %s successfully created at location %s" % (currentRepo, repos[currentRepo]['location']))
      errorMessage = ("Error creating repository %s" % repos[currentRepo]['location'])

  if args.action == 'prune':
      # Clean up repo according to provided preservation policy
      command = resticLocation + ' forget --group-by host --keep-within ' + \
          repos[currentRepo]['max_age'] + 'd --prune --repo ' + \
          repos[currentRepo]['location']
      result = run_command(command, commandEnv)
      # Return the results
      successMessage = ("Repository %s clean up successful" % currentRepo)
      errorMessage = ("Error cleaning up repository %s" % currentRepo)

  elif args.action == 'check':
      # Check the repository integrity
      command = resticLocation + ' check --repo ' + repos[currentRepo]['location']
      if args.full:
          command = command + ' --read-data'
      result = run_command(command, commandEnv)
      # Check the restic return code
      errorMessage = ''
      if not result.returncode == 0:
          errorMessage = ("Error checking repository %s" % currentRepo)
      else:
          # If requested, check the snapshots age
          if args.age:
              command = resticLocation + ' snapshots --json --group-by host --repo ' + \
                  repos[currentRepo]['location']
              result2 = run_command(command, commandEnv)
              if not result2.returncode == 0:
                  errorMessage = (
                      "Error getting snapshots for repository %s" % currentRepo)
                  result.stderr = result.stderr + "\n" + result2.stderr
                  result.returncode = 2
              else:
                  snaps = json.loads(result2.stdout)
                  # Oldest snapshot is the first one
                  oldestTime = snaps[0]['snapshots'][0]['time']
                  # Newest snapshot is the last one
                  newestTime = snaps[0]['snapshots'][len(
                      snaps[0]['snapshots'])-1]['time']
                  # Convert to Pythonic time structures
                  timeFormat = '%Y-%m-%dT%H:%M:%S'
                  oldestTime = datetime.strptime(oldestTime[0:18], timeFormat)
                  newestTime = datetime.strptime(newestTime[0:18], timeFormat)
                  # Compute snapshots ages versus the current time
                  currentTime = datetime.now()
                  oldDiff = currentTime - oldestTime
                  newDiff = currentTime - newestTime
                  # Check ages versus config
                  if oldDiff > timedelta(days=int(repos[currentRepo]['max_age'])):
                      errorMessage = (
                          "Oldest snapshot on %s is %s old" % (currentRepo, oldDiff))
                  if newDiff > timedelta(days=int(repos[currentRepo]['min_age'])):
                      errorMessage = (
                          "Newest snapshot on %s is %s old" % (currentRepo, newDiff))
                  else:
                      result.stdout = result.stdout + "\n" + \
                          ("Newest snapshot age: %s" % newDiff) + \
                          "\n" + ("Oldest snapshot age: %s" % oldDiff)
      # Return the results
      successMessage = ("Repository %s is healthy" % currentRepo)
      # errorMessage is already defined

  elif args.action == 'list':
      # List snapshots in the repo
      command = resticLocation + ' snapshots --group-by host --repo ' + \
          repos[currentRepo]['location']
      result = run_command(command, commandEnv)
      # Return the results
      successMessage = ("Snapshot list retreived for repository %s" % currentRepo)
      errorMessage = ("Error listing snapshots on repository %s" % repos[currentRepo]['location'])

  else:
      # If this is a duplicate type repo, we copy snapshots from the source to the destination
      if 'duplicate' in repos[currentRepo].keys():
        command = resticLocation + ' copy --repo2 ' + repos[currentRepo]['location'] + ' --repo ' + repos[duplicateSource]['location']
        result = run_command(command, commandEnv)
        # Swap the repositories password to enable the unlock
        commandEnv["RESTIC_PASSWORD"] = commandEnv["RESTIC_PASSWORD2"]

      # For a standard repo, create a new snapshot
      else:
        command = resticLocation + ' backup --exclude \'lost+found\' --repo ' + repos[currentRepo]['location']
        # Incorporate includes (mandatory)
        for folder in repos[currentRepo]['includes']:
          command = command + ' ' + folder
        # Incorporate excludes if present
        if 'excludes' in repos[currentRepo].keys():
          for folder in repos[currentRepo]['excludes']:
            command = command + ' --exclude="' + folder + '"'
        result = run_command(command, commandEnv)        
      
      # Return the results
      successMessage = ("Snapshot successfully created on repository %s" % currentRepo)
      errorMessage = ("Error creating new snapshot on repository %s" % repos[currentRepo]['location'])

  # At the end of each loop round, we accumulate the outputs to prepare the final script output
  if not result.returncode == 0:
    scriptReturnValue = 2
  successMessageAccumulated += successMessage + ". "
  errorMessageAccumulated += errorMessage + ". "
  stdoutAccumulated += result.stdout
  stderrAccumulated += result.stderr

  # Ensure the repository is unlocked
  command = resticLocation + ' unlock --repo ' + repos[currentRepo]['location']
  resultUnlock = run_command(command, commandEnv)
  stdoutAccumulated += resultUnlock.stdout
  stderrAccumulated += resultUnlock.stderr
  if scriptReturnValue < 2 and not resultUnlock.returncode == 0:
    scriptReturnValue = 1

# Provide the user output
end_script(
  scriptReturnValue,
  stdoutAccumulated,
  stderrAccumulated,
  successMessageAccumulated,
  errorMessageAccumulated,
  args.quiet,
  args.verbose
)