# TLE

TLE is a Discord bot centered around Competitive Programming.

## Features

The features of the bot are split into a number of cogs, each handling their own set of commands.

### Codeforces cogs

- **Codeforces** Commands that can recommend problems or contests to users, taking their rating into account.
- **Contests** Shows details of upcoming/running contests.
- **Graphs** Plots various data gathered from Codeforces, e.g. rating distributions and user problem statistics.
- **Handles** Gets or sets information about a specific user's Codeforces handle, or shows a list of Codeforces handles.

### Other cogs

- **Starboard** Commands related to the starboard, which adds messages to a specific channel when enough users react with a ⭐️.
- **CacheControl** Commands related to data caching.

## Installation

> If you want to run the bot inside a docker container follow these [instructions](/Docker.md)

Clone the repository

```bash
git clone https://github.com/cheran-senthil/TLE
```

### Dependencies

Now all dependencies need to be installed. TLE uses [Poetry](https://poetry.eustace.io/) to manage its python dependencies. After installing Poetry navigate to the root of the repo and run

```bash
poetry install
```

> :warning: **TLE requires Python 3.7 or later!**

If you are using Ubuntu with older versions of python, then do the following:

```bash
apt-get install python3.7-venv libpython3.7-dev
python3.7 -m pip install poetry
python3.7 -m poetry install
```

---

#### Library dependencies

TLE also depends on cairo and pango for graphics and text rendering, which you need to install. For Ubuntu, the relevant packages can be installed with:

```bash
apt-get install libcairo2-dev libgirepository1.0-dev libpango1.0-dev pkg-config python3-dev gir1.2-pango-1.0
```

Additionally TLE uses pillow for graphics, which requires the following packages:

```bash
apt-get install libjpeg-dev zlib1g-dev
```

### Final steps

You will need to setup a bot on your server before continuing, follow the directions [here](https://github.com/reactiflux/discord-irc/wiki/Creating-a-discord-bot-&-getting-a-token). Following this, you should have your bot appearing in your server and you should have the Discord bot token. Finally, go to the `Bot` settings in your App's Developer Portal (in the same page where you copied your Bot Token) and enable the `Server Members Intent`.

Create a new file `environment`.

```bash
cp environment.template environment
```

Fill in appropriate variables in new "environment" file.

#### Environment Variables

- **BOT_TOKEN**: the Discord Bot Token for your bot.
- **LOGGING_COG_CHANNEL_ID**: the [Discord Channel ID](https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID-) of a Discord Channel where you want error messages sent to.
- **ALLOW_DUEL_SELF_REGISTER**: boolean value indicating if self registration for duels is enabled.
- **TLE_MODERATOR**: the name of the role that can run moderator commands of the bot. If this is not set, the role name will default to "Moderator".
- **STORAGE_BUCKET**(Optional): the link of firebase storage bucket for saving backups. Note: this variable is only required when you want to enable automated backups to firebase storage (If you are deploying on heroku, you need to enable this otherwise you might lose data every 24 hrs).
- **CLIST_API_TOKEN**: https://clist.by is used to fetch list of upcoming contests, just replace your clist username and api key into the environment file.

#### For Setting Up Backups to Firebase Storage

You will need to create a new firebase project to store backups of your database.
After creating a project go to project settings -> service accounts and then click on create new service account if there isn't one already creted and finally click on generate new private key. Rename this file to 'firebase-admin.json' and place it inside the bot's folder.

To start TLE just run:

```bash
./run.sh
```

### Notes

- In order to run admin-only commands, you need to have the `Admin` role, which needs to be created in your Discord server and assign it to yourself/other administrators.
- In order to prevent the bot suggesting an author's problems to the author, a python file needs to be run (since this can not be done through the Codeforces API) which will save the authors for specific contests to a file. To do this run `python extra/scrape_cf_contest_writers.py` which will generate a JSON file that should be placed in the `data/misc/` folder.
- In order to display CJK (East Asian) characters for usernames, we need appropriate fonts. Their size is ~36MB, so we don't keep in the repo itself and it is gitignored. They will be downloaded automatically when the bot is run if not already present.
- One of the bot's features is to assign roles to users based on their rating on Codeforces/CodeChef. In order for this functionality to work properly, the following roles need to exist in your Discord server for codeforces and codechef based roles respectively
  - Newbie
  - Pupil
  - Specialist
  - Expert
  - Candidate Master
  - Master
  - International Master
  - Grandmaster
  - International Grandmaster
  - Legendary Grandmaster
  - 1 Star
  - 2 Star
  - 3 Star
  - 4 Star
  - 5 Star
  - 6 Star
  - 7 Star
    You don't need to manually create the roles instead your can use the following commands
    `;createroles codeforces` and `;createroles codechef`.
    For now, codechef based roles are not updated automatically instead you'll need to run `;roleupdate codechef` after every contest's rating changes are published. Since, the frequency of contests is low on codechef, the moderators can take care of it.
- One of the bot's commands require problemsets to be cached. Run `;cache problemsets all` at the very first time the bot is used. The command may take around 10 minutes to run.

## Usage

In order to run bot commands you can either ping the bot at the beginning of the command or prefix the command with a semicolon (;), e.g. `;handle pretty`.

In order to find available commands, you can run `;help` which will bring a list of commands/groups of commands which are available. To get more details about a specific command you can type `;help <command-name>`.

## Contributing

Pull requests are welcome. For major changes please open an issue first to discuss what you would like to change.

Before submitting your PR, consider running some code formatter on the lines you touched or added. This will help reduce the time spent on fixing small styling issues in code review. Good options are [yapf](https://github.com/google/yapf) or [autopep8](https://github.com/hhatto/autopep8) which likely can be integrated into your favorite editor.

Please refrain from formatting the whole file if you just change some small part of it. If you feel the need to tidy up some particularly egregious code, then do that in a separate PR.

## License

[MIT](https://choosealicense.com/licenses/mit/)
