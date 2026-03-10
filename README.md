# Red Dead Redemption 2 Steam Deck/Linux toolbox
I started developping it after modding my rdr2 install through Pikdum's tools (https://github.com/pikdum/steam-deck), at first I was like, this shit is crazy and then I saw how complicated it was just to install some mods for them not to work, so I
build this minimalist (for now) toolbox. For now, you can edit your save file with money and honor, lookup for mods on nexus and install them, it's quite broken because of cloudflare and stufdf but you can also just download the mod manually and the app downloads the hook and mod loaders automatically and detecets your install, it maintains it, deploy the mods. You need to run the .sh first to get the dependency and stuff, if something doesn't work, tell me! 
Have a good ride and simple ride!

# Warnings
This is definitly not production ready and I'm currently working on retro engineering the rdr2 save edit mod (https://www.nexusmods.com/reddeadredemption2/mods/55) to port it's main algorithms allowing to modify saves without corrupting them to Linux, for now, do not use the current save modifiers tools, they only work once and are not reliable, you can do it as it copies the file, you will not lose progression but your main save only.

# Roadmap
- Enhance the mod downloader to be able to download mods from nexus, like for real this time
- Update the script to be able to download the latest version of the mods/update them
- Update the preview
- Save modifier
- New stuff

# How to use it
- Run the preview.py file to see what it does (optional)
- Run the setup_alias.sh file to setup needed stuff
- Run the rdr2_toolbox.py file to use it

# Features
- **Save Editing**: Modify money and honor (experimental)
- **Mod Management**: Automatic detection of game installation and deployment of mods.
- **Dependency Handling**: Automated setup of mod loaders and hooks.
- **Nexus Integration**: Search for mods directly within the tool.

# Contributing
Contributions are welcome! If you have ideas for the save editing algorithm or want to help with the Nexus scraper, feel free to open an issue or a pull request.

# Current known bugs
- The save modifier corrupts the file
- Help me find the rest!
