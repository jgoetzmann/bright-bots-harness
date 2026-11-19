# Machines: self-hosted runners that sleep when idle

A *machine* is one Linux box that runs the night bot's model jobs for the subscriptions that live on
it: a virtual machine on AWS, Azure or Google Cloud, or a computer of your own. It wakes when a job
waits for it and powers itself off after 30 idle minutes, so you pay for the hours it works and for
its disk. Adapted from a production night bot's machine.

Most setups need none: a provider whose login fits in a GitHub secret runs on GitHub's own runners
(`"runs_on": "ubuntu-latest"` in [`../providers.json`](../providers.json)). Read on if one does not.

## GitHub's runners or a machine?

| | GitHub's runners (`ubuntu-latest`) | A machine (`nightbot-<id>`) |
|---|---|---|
| Login | `"login": "secret"`: a token or key held in a repository secret (Claude's `claude setup-token`, an API key) | `"login": "machine"`: a login the CLI keeps in a file and refreshes in place (Codex or Gemini CLI signed in with your account), which cannot travel as a secret, since each refresh replaces it |
| Isolation | a fresh VM for every job | one box that persists: each subscription has its own Linux user and home, and a job can leave files for that subscription's next job |
| Cost | free minutes on public repositories, metered on private ones (GitHub's Actions pricing) | the cloud's hourly price while it runs, its disk always (below); nothing from GitHub's hosted minutes |
| Limits | 6 hours a job; a few vCPUs | as big as you pay for; jobs share its CPU, memory and disk |
| Upkeep | none | these scripts, run once and then when you update |

So: a machine when a CLI's login must live on a computer, or when you want a bigger or always-ready
box; GitHub's runners for everything else. The plan and deliver jobs, which hold GitHub write
tokens, always run on GitHub's runners; only the model job (`work` in
`.github/workflows/nightbot-run.yml`) runs on a machine.

## How it works

- **A user per subscription.** Each machine subscription `<id>` (a `providers.json` entry whose
  `runs_on` is `nightbot-<id>`) has a Linux user `agent-<id>`: a home only it can read (mode 700),
  no `sudo`, no `docker` group. Its CLI login lives in that home, made once by a person
  (`sudo -iu agent-codex codex login --device-auth`).
- **A runner per lane.** The user runs one GitHub Actions runner per `lanes`:
  `~agent-<id>/actions-runner` named `nightbot-<id>`, `~agent-<id>/actions-runner-2` named
  `nightbot-<id>-2`, and so on, each a systemd service under that user, each with the one label
  `nightbot-<id>`. The work job asks for `runs-on: nightbot-<id>`, so it lands only on that
  subscription's runners, and runs as its user, with its login.
- **The idle stop.** Every five minutes a timer asks whether anything uses the machine: a job, a
  person logged in, a script sent by `on-machine.sh`, first boot, apt. After `IDLE_MINUTES` (30) of
  none it stops the machine: `shutdown -h now` on AWS and Google Cloud (the instance is stopped,
  not billed for compute), a self-deallocation on Azure (an OS shutdown would leave an Azure VM
  allocated and billed), nothing on a machine of your own unless you ask.
- **The starter.** A small cloud function runs every five minutes. If the machine is stopped and a
  job of `nightbot-run.yml` has been queued for a `nightbot-*` label for less than three hours, it
  starts the machine; the runner takes the job when it comes up (GitHub keeps a job queued for a
  self-hosted runner up to 24 hours). A job older than three hours is left alone: its runner is
  missing, and starting the machine would only keep it on for nothing. The starter may start that
  one machine and read it, nothing else.
- **The disk.** All jobs share one disk. After every job (the runners' job-completed hook) and
  every ten minutes, `clean.sh` gives back what comes back by itself: that job's temporary files,
  package caches once no other job of that user runs, checkouts untouched for a week, the runner
  version a self-update replaced, old logs. The journal is capped at 200 MB.
- **Never mid-job.** Nothing here restarts a runner that is running a job (that kills the job):
  Ubuntu's `needrestart` is told to leave runners alone, and `setup.sh` and `register-runners.sh`
  restart only idle runners, telling you which to finish later.

## The files

| File | Runs | What it does |
|---|---|---|
| `common/setup.sh` | on the machine, as root | everything above but the logins and the registration; idempotent, run again to update |
| `common/register-runners.sh` | your computer | registers each subscription's runners with GitHub, through `runners-on-box.sh` on the machine |
| `common/clean.sh`, `common/idle-stop.sh` | on the machine | the disk clean-up and the idle stop, installed by `setup.sh` |
| `common/extra-setup.sh`, `common/extra-user-setup.sh` | on the machine | your repository's toolchain, as root and as each agent user; shipped as commented examples |
| `common/bundle.sh` | your computer | how every `on-machine.sh` ships a script with its directory and `providers.json` |
| `<cloud>/provision.sh` | your computer | creates the machine (aws, azure, gcp) |
| `<cloud>/on-machine.sh` | your computer | runs a script on the machine as root, starting it first (aws, azure, gcp, local) |
| `<cloud>/deploy-starter.sh` | your computer | deploys the starter (aws, azure, gcp) |
| `<cloud>/budget.sh` | your computer | a monthly budget with email alerts (aws, azure, gcp) |
| `starter/core.py`, `aws/starter.py`, `azure/starter.py` + `function_app.py`, `gcp/main.py` | the cloud | the starter: the GitHub side, and each cloud's few lines |
| `starter/test_*.py` | your computer | the tests: `cd .nightbot/machine/starter && python3 -m unittest -v` |

Every script's header says where it runs, what it needs and which settings it takes. On your
computer they need bash, `jq`, `python3`, `gh` signed in as an admin of the repository, and the
cloud's own CLI signed in. Run them from anywhere; the examples run from the repository's root.
The ids below are an example: `codex` and `gemini` as machine subscriptions.

## Before you start

1. In `.nightbot/providers.json`, give each machine subscription `"login": "machine"` and
   `"runs_on": "nightbot-<id>"` (the id lower-case: it names the Linux user), and `lanes`, the jobs
   it may run at once. Its `cli` (`claude`, `codex` or `gemini`) tells `setup.sh` what to install;
   another CLI needs an `"install"` command in its `clis` entry in `config.json` (run as root), or
   `extra-setup.sh`.
2. In the repository's **Settings → Actions → General**, under approval for fork pull request
   workflows, choose **Require approval for all external contributors** (Security, below).
3. If your checks need tools on the machine (a compiler, a language runtime), put them in
   `common/extra-setup.sh` (system-wide, as root) or `common/extra-user-setup.sh` (in each agent's
   home), where the work job's setup steps would otherwise install them.

## AWS

Needs the AWS CLI v2 signed in (`aws configure sso`, or keys), as someone who may create IAM roles,
EC2 instances and Lambda functions, and for interactive shells the CLI's Session Manager plugin.

1. **Provision**: an `m7i-flex.large` (2 vCPUs, 8 GB) with Ubuntu 24.04 and an encrypted 100 GB
   gp3 disk; a role with Systems Manager only; a security group with no inbound rule; no key pair;
   IMDSv2; shutdown behaviour `stop`.
   ```sh
   REGION=us-east-1 .nightbot/machine/aws/provision.sh
   ```
   `INSTANCE_TYPE`, `DISK_GB` and `NAME` (default `nightbot-box`) change it; on a new account's free
   plan, `m7i-flex.large` is among the types allowed.
2. **Set it up** (every machine subscription; or name the ids):
   ```sh
   .nightbot/machine/aws/on-machine.sh .nightbot/machine/common/setup.sh --cloud aws
   ```
3. **Log in** each subscription once, as its user. Open a shell with
   `aws ssm start-session --target <instance id>` (provision.sh printed it), then:
   ```sh
   sudo -iu agent-codex codex login --device-auth
   sudo -iu agent-gemini env NO_BROWSER=true gemini     # sign in with Google, then /quit
   ```
   Each prints a URL to open on your computer. The machine does not stop while you are logged in.
4. **Register the runners**:
   ```sh
   CLOUD=aws .nightbot/machine/common/register-runners.sh OWNER/REPO
   ```
   They appear under Settings → Actions → Runners, idle.
5. **Deploy the starter** (a Lambda every five minutes; for a private repository add
   `STARTER_GITHUB_TOKEN`, a fine-grained token with read access to Actions on it, nothing else):
   ```sh
   REPO=OWNER/REPO .nightbot/machine/aws/deploy-starter.sh
   ```
6. **Set a budget**, so a machine that never stops cannot surprise you:
   ```sh
   BUDGET_USD=40 ALERT_EMAIL=you@example.com .nightbot/machine/aws/budget.sh
   ```

## Azure

Needs the Azure CLI signed in (`az login`, then `az account set --subscription …`) as Owner, or
Contributor plus User Access Administrator: the scripts create custom roles.

1. **Provision**: a `Standard_D2as_v5` (2 vCPUs, 8 GB), Ubuntu 24.04, 100 GB Standard SSD, in
   resource group `nightbot`; no public IP; a network security group with no inbound rule; a
   system-assigned identity that may deallocate this VM and nothing else; boot diagnostics, for the
   serial console.
   ```sh
   LOCATION=eastus .nightbot/machine/azure/provision.sh
   ```
   It ends by checking, through Run Command, that the VM reaches GitHub. The subnet uses Azure's
   default outbound access, which is free; if your subscription has none (Azure makes new subnets
   private by default), run it again with `OUTBOUND=nat` for a NAT gateway (about $35 a month).
2. **Set it up**:
   ```sh
   .nightbot/machine/azure/on-machine.sh .nightbot/machine/common/setup.sh --cloud azure
   ```
   Run Command returns only the last 4,096 bytes of output (the summary); the whole log stays on
   the VM in `/var/log/nightbot/`.
3. **Log in** each subscription once, through the serial console (nothing reaches the VM's port 22).
   Give the admin user a password once, then connect:
   ```sh
   az vm user update -g nightbot -n nightbot-box -u nightbot-admin -p '<a long password>'
   az serial-console connect -g nightbot -n nightbot-box     # or the portal's Serial console
   ```
   Log in as `nightbot-admin`, then run the logins of AWS step 3.
4. **Register the runners**: `CLOUD=azure .nightbot/machine/common/register-runners.sh OWNER/REPO`
5. **Deploy the starter** (an Azure Function, Flex Consumption where offered):
   `REPO=OWNER/REPO .nightbot/machine/azure/deploy-starter.sh` (and `STARTER_GITHUB_TOKEN` for a
   private repository).
6. **Set a budget** on the resource group:
   `BUDGET_USD=40 ALERT_EMAIL=you@example.com .nightbot/machine/azure/budget.sh`

## Google Cloud

Needs `gcloud` signed in to a project with billing (`gcloud config set project …`), as Owner, or
with the roles to enable APIs and create networks, VMs, service accounts and functions. Connecting
takes `roles/compute.osAdminLogin` and `roles/iap.tunnelResourceAccessor` (Owner has both).

1. **Provision**: an `e2-standard-2` (2 vCPUs, 8 GB), Ubuntu 24.04, 100 GB balanced disk; no
   external IP; a network whose only inbound rule lets in IAP's range on port 22; Cloud NAT for the
   way out; no service account; Shielded VM; OS Login.
   ```sh
   PROJECT=my-project ZONE=us-central1-a .nightbot/machine/gcp/provision.sh
   ```
2. **Set it up**:
   ```sh
   .nightbot/machine/gcp/on-machine.sh .nightbot/machine/common/setup.sh --cloud gcp
   ```
3. **Log in** each subscription once:
   `gcloud compute ssh nightbot-box --zone us-central1-a --tunnel-through-iap`, then the logins of
   AWS step 3.
4. **Register the runners**: `CLOUD=gcp .nightbot/machine/common/register-runners.sh OWNER/REPO`
5. **Deploy the starter** (a Cloud Run function, called every five minutes by Cloud Scheduler):
   `REPO=OWNER/REPO .nightbot/machine/gcp/deploy-starter.sh` (and `STARTER_GITHUB_TOKEN` for a
   private repository). If its build fails on a permission, the project's build service account
   lacks `roles/cloudbuild.builds.builder`: grant it, and run it again.
6. **Set a budget** (alerts go to the billing account's administrators, and to `ALERT_EMAIL`):
   `BILLING_ACCOUNT=<id> BUDGET_USD=40 .nightbot/machine/gcp/budget.sh`
   (`gcloud billing accounts list` shows the id).

## A machine of your own

A home server, a spare PC, or a Linux VM on your Mac (UTM, Multipass, OrbStack …): Ubuntu 22.04 or
24.04 with systemd, on all the time or woken by you. `setup.sh` changes the system as for a
dedicated box (swap, journal size, user namespaces for the CLIs' sandboxes), so give the night bot
a machine, or a VM, of its own. There is no starter: a job waits until the machine is on.

1. **Set it up**, over ssh (`HOST`, whose user needs `sudo` without a password) or on the machine
   itself (leave `HOST` out):
   ```sh
   HOST=me@homeserver .nightbot/machine/local/on-machine.sh .nightbot/machine/common/setup.sh --cloud local
   ```
   It stays on. Add `IDLE_STOP=suspend` (or `poweroff`) after `--cloud local` to make it sleep
   after `IDLE_MINUTES` idle; wake it yourself (Wake-on-LAN, a button).
2. **Log in** each subscription on the machine, as in AWS step 3.
3. **Register the runners**:
   `CLOUD=local HOST=me@homeserver .nightbot/machine/common/register-runners.sh OWNER/REPO`

## Running it

- **Look at it.** On GitHub: Settings → Actions → Runners. On the machine:
  ```sh
  systemctl list-units 'actions.runner.*'     # the runners
  sudo journalctl -t nightbot                 # what the idle stop did
  sudo journalctl -t nightbot-clean -n 20     # the disk clean-ups
  sudo ls /var/log/nightbot/                  # the last run of each script on-machine.sh sent
  df -h /; free -h
  ```
  The starter's log: `aws logs tail /aws/lambda/nightbot-box-starter`; the function app's log
  stream in the Azure portal; `gcloud functions logs read nightbot-box-starter --gen2 --region …`.
- **Update the CLIs, or change a setting**: run step 2 again, with any setting as an argument
  after the ids (`… setup.sh --cloud aws IDLE_MINUTES=45`; the settings are in `setup.sh`'s
  header). A run keeps the settings the last one made unless given again. The CLIs install at
  `@latest`; idle runners whose settings changed restart, busy ones say so and keep their job.
- **Add a subscription.** Add its `providers.json` entry, run step 2 (with no ids it sets up every
  machine subscription), log it in, and run `register-runners.sh` again.
- **More lanes.** Raise its `lanes` and run `register-runners.sh`; `id=n` sets a count for this
  machine only, `id=0` removes its runners (before you delete an entry). Each lane is one more job
  at once on the same CPU, memory and disk: a 2 vCPU, 8 GB machine holds two or three jobs that
  build and test; give it more, or use a bigger type (stop it, change the type, start it).
- **A login stopped working** (the job says it was refused): log that user in again. Nothing else
  changes.
- **Several machines.** Make the second with `NAME=nightbot-box-2`, and pass that `NAME` to every
  script for it. If it carries a label the first also carries, register it with `BOX=b2` (its
  runners are named `…-b2`: GitHub allows one runner of a name) and counts of its own
  (`codex=1`). Its starter takes `RUNNER_LABELS=nightbot-codex,…`, the labels it serves; a starter
  without `RUNNER_LABELS` wakes its machine for every `nightbot-*` job, and a machine that finds no
  job stops again once idle.
- **Pause it.** Stop the starter (`aws events disable-rule --name nightbot-box-starter`;
  `az functionapp stop -g nightbot -n <app>`; `gcloud scheduler jobs pause nightbot-box-starter
  --location <region>`), and the machine stops itself within `IDLE_MINUTES`.
- **The starter does not start it.** Its log says why: a 404 from GitHub means a private repository
  without `STARTER_GITHUB_TOKEN` (or a token that has expired); a job queued more than three hours
  is ignored; a runner label must start with `nightbot-` (or be in `RUNNER_LABELS`).
- **The disk fills up.** `sudo nightbot-clean` runs the clean-up now; `du -xsh /home/agent-*/*`
  shows who holds what. Grow the cloud disk in place (AWS `aws ec2 modify-volume`, Azure
  `az disk update --size-gb` while deallocated, GCP `gcloud compute disks resize`); `setup.sh` grows
  the partition and filesystem into it.
- **Take it down.** Remove the runners (`register-runners.sh OWNER/REPO codex=0 gemini=0`), then:
  AWS, terminate the instance and delete the Lambda, its EventBridge rule, the two roles, the
  instance profile and the security group (all named after `NAME`); Azure,
  `az group delete -n nightbot` and the custom roles (`az role definition list --custom-role-only
  true`); Google Cloud, delete the VM, the function, the scheduler job, the router and the network.

## What it costs

Rough on-demand prices for the defaults in a US region; your region, plan and credits change them,
so check the cloud's calculator. A stopped machine costs only its disk.

| | AWS | Azure | Google Cloud | Your own |
|---|---|---|---|---|
| Machine, 2 vCPUs, 8 GB, an hour running | `m7i-flex.large` ≈ $0.096 | `D2as_v5` ≈ $0.086 | `e2-standard-2` ≈ $0.067 | electricity |
| Disk, 100 GB, a month (always) | gp3 ≈ $8 | Standard SSD E10 ≈ $10 | balanced ≈ $10 | |
| Way out | public IPv4, $0.005 an hour running | default outbound: free (a NAT gateway ≈ $35 a month) | Cloud NAT: its address up to ≈ $4 a month, $0.045 a GB | |
| Starter | within Lambda's free tier | within Flex Consumption's free grant | within Cloud Run's free tier; Scheduler's 3 free jobs | none |
| 100 hours running a month | ≈ $18 | ≈ $18 | ≈ $21 | |
| Never stopping | ≈ $82 | ≈ $72 | ≈ $64 | |

The night bot's own spend is the model subscriptions; the machine adds the line above.

## Security

- **No way in from outside, where the cloud allows it.** AWS: a security group with no inbound rule
  and no key pair; the way in is Session Manager and Run Command, through the instance's role,
  which allows nothing else. Azure: no public IP and no inbound rule; the way in is Run Command and
  the serial console, both through Azure's control plane and your Azure role. Google Cloud: no
  external IP; the one inbound rule admits IAP's range on port 22, and OS Login checks your Google
  account's roles. Your own: what your network allows.
- **A user per subscription.** Each login sits in a home only its user can read; a job runs as
  that user and can read only its own login and files. No agent user has `sudo` or Docker. Keep
  other secrets off the machine: a model's session can read whatever its user can.
- **A label per subscription.** A runner carries only `nightbot-<id>`, so only that subscription's
  jobs land on it. The runners belong to this repository alone.
- **Who can run code there.** Anyone who can push a workflow to the repository can send a job to
  these labels, and with it read that subscription's login: give write access only to people you
  would trust with the logins. In Settings → Actions → General, choose **Require approval for all
  external contributors**, so no pull request from a fork runs a workflow until a maintainer
  approves it. Never put self-hosted runners on a public repository without that.
- **Least privilege around it.** The machine's own cloud identity can do nothing (AWS: Systems
  Manager only; Azure: deallocate itself; Google Cloud: no service account). The starter can read
  and start that one machine. Its GitHub token, when a private repository needs one, should be
  fine-grained, for this repository only, with read access to Actions only.
- **Registration tokens** last an hour and travel inside the shipped bundle, not on a command line:
  `runners-on-box.sh` hands them to the runner's `config.sh` in its environment, so another user's
  `ps` never shows them. They do appear in the cloud's record of the command (Run Command history),
  which only your cloud account's administrators can read.
- **Updates.** Ubuntu's unattended upgrades stay on; a runner takes new libraries at its next
  start, and the nightly stop and start brings a new kernel in.
