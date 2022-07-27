#WebStereo

## WebStereo is a program to manage a music library with a browser-based interface.

---

## I. Description and Backstory

When Apple designated iTunes as end-of-life in favor Music.app (centered around a subscription service), I had approximately 60 GB of music files in a variety of formats. I knew that I would need to find a new way to manage them, something similar to the old, non-cloud-based iTunes. This is that program.

---

## II. Installation and Use

1. Clone this repository with the following command:
        - `git clone https://github.com/thisisrobertr/webstereo`

2. Create a Python virtual environment with the following commands (this step is technically optional, but makes some things easier to troubleshoot and keeps the system Python environment spare and organized.
         `python3 -m venv venv/`
      `source venv/bin/activate`

3. Install Dependencies
    - WebStereo has one main external dependency: `ffmpeg`, with `ffplay` included. To get the `ffplay` command, I had to build ffmpeg from source - the Homebrew package did not include it. If you choose to install from source, be advised that `make` will silently fail to compile the `ffplay` component of the package unless you have sdl2 installed.
    
4. Configure WebStereo.
    - There are two main ways WebStereo can be configured: editing config.json directly, and using the command-line options of data.py. For the first method, run data.py with -c to generate a file with the default parameters, and open the new config.json in an editor. For the second, run `data.py -h` to view the available commands.
 
5. Build the Music Database
    - Once you have specified the location of your music library in config.json, run `python3 data.py -b -a` to add your albums to the WebStereo library.
    
    - The first versions of WebStereo were used on an existing iTunes media library, and the import engine is very much designed with that in mind: it expects that you have folders for each artist, inside of which there is a folder for each album containing the audio files for each song. Files outside of this directory structure will not be included.
     
6. Run WebStereo.
    - Run `python3 webstereo.py` or deploy it to your server configuration
    
    - Open the application in a web browser. By default, its URL is localhost:8000. NB that by default, it listens only on localhost - to use it across a LAN, you will need to set the host in config.json to 0.0.0.0

---

## III. Considerations and Notes
- While there is support for using a password to access WebStereo (it can be enabled and disabled in configuration), it is very much designed for a single-user environment.

- At present, supported audio formats include the following: AAC/M4A, MP3, AIFF/AIFC, FLAC, WAVE, and OGG. If possible, metadata will be extracted from the files, otherwise file/folder names and such will be used to guess at title, artist, album, and track number.

- Currently, there is no built-in mechanism for importing new audio; adding songs means modifying the filesystem and rebuilding the entire database.

- Despite my best efforts to date, WebStereo has not moved beyond its origins as a tool I wrote to fulfill a personal need - there are still several missing features and imperfections in it. Please take it in that context.
