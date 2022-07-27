import sys
import data
import time
import queue
import threading
import random
import shutil
import subprocess
import logging
import data


db = data.WebStereoDB  # there is no need for a database object, but this is used to simplify references to its static constants.

#Initialize logging
logging.basicConfig(format='%(asctime)s %(levelname)s %(filename)s %(funcName)s:%(lineno)d %(name)s %(message)s')
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


class NoAudioIOAvailableError(Exception):
    pass


# On Darwin, check whether we have access to FFMPEG. If we don't, fallback to afplay, but that doesn't support seeking.
USE_FFPLAY = False
if shutil.which('ffplay'):
    USE_FFPLAY = True
elif shutil.which('ffmpeg') and sys.platform not in ['win32', 'darwin']:
    # Linux-specific ffmpeg trick using ALSA
    USE_FFPLAY = False  # technically, we still use ffmpeg, but we can't do anything else.
else:
    raise NoAudioIOAvailableError('unable to locate a suitable program to do audio I/O')


class AudioController:
    def __init__(self, db):
        self.db = db
        self.up_next = queue.Queue() # Store songs to be played next
        self.up_prev = []  # Store previously played songs
        self.playing = False
        self.paused = False
        self.filename = ''
        self.song = ''
        self.album = ''
        self.track = 0
        self.length = ''
        self.start_time = 0
        self.proc = None
        self.reset_metadata()
        self.paused_time = 0  # Used to track when playback was paused
        self.shuffle_pool = []  # stores songs to play when on shuffle
        self.shuffle_pool_size = 0  # size of preceding array
        self.shuffle_on = False  # track whether we are in random shuffle mode
        
    def reset_metadata(self):
        #if self.album != 'Not playing' and self.song != 'Not playing' and self.album != '' and self.song != '':
        #    # prevent adding blank strings or "Not playing" to the list of previously played tracks
        #    self.up_prev.append([self.song, self.album])
        self.playing = False
        self.filename = ''
        self.song = 'Not playing'
        self.album = 'Not playing'
        self.track = 0
        self.length = '0:00'

    def play_file(self, filename):
        self.filename = filename
        self.play()

    def play(self, time_continue=0):
        if self.proc:
            self.proc.kill()

        if USE_FFPLAY:
            self.proc = subprocess.Popen(['ffplay', '-nodisp', '-loglevel', 'error', '-ss', str(time_continue), self.filename], stdout=None, stdin=None, stderr=None)
        else:
            self.proc = subprocess.Popen(['ffmpeg', '--hide-banner', '-sS', str(time_continue), '-i', self.filename, '-f', 'alsa', 'default'], stdout=None, stdin=None, stderr=None)

        self.playing = True
    def _play(self, time_continue=0):
        try:
            self.proc.kill()
        except AttributeError:
            if self.proc == None:
                #self.proc = subprocess.Popen(['/Applications/VLC.app/Contents/MacOS/VLC', '--intf', 'ncurses', self.filename], stdout=None, stdin=None, stderr=None)
                # self.proc = subprocess.Popen(['ffplay', '-nodisp', '-loglevel', 'error', '-stats', '-ss', str(time_continue), self.file])
                # Use FFMPEG/FFPLAY if we have it
                if USE_FFMPEG:
                    self.proc = subprocess.Popen(['ffplay', '-nodisp', '-loglevel', 'error', '-ss', str(time_continue), self.filename])
                else:
                    self.proc = subprocess.Popen(['afplay', self.filename])  # afplay does not support seeking, to my knowledge
                    
        else:  # Linux/BSD/UNIX arts-and-crafts
            if self.proc == None:  # Do not under any circumstances start more than one subprocess at a time. If you do that, playback will overlap multiple times.
                log.debug('beginning track playback')
                self.proc = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'fatal', '-ss', str(time_continue), '-i', self.filename, '-f', 'alsa', 'default'], stdout=None, stdin=None, stderr=None)
            else:
                # If a process is running, kill and restart it with the new parameters. If this condition does not exist, playback will on occasion not start
                # set stdout and stderr to none so that ncurses/other stylized output etc will not leave the terminal in an unknown state.
                self.kill_proc()
                self.proc = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'fatal', '-ss', str(time_continue), '-i', self.filename, '-f', 'alsa', 'default'], stdout=None, stdin=None, stderr=None)
                
        self.playing = True

    def stop(self):
        self.kill_proc()
        self.reset_metadata()

    def pause(self):
        self.kill_proc()  # Stop playback
        self.paused_time = int(time.time()) - self.start_time  # Store progression of song when paused
        self.paused_timestamp = int(time.time())  # Store time song is paused, so that the counter in the front end may resume properly.
        self.paused = True  # Set paused flag to set other bits into motion

    def resume(self):
        self.play(self.paused_time)
        diff = int(time.time()) - self.paused_timestamp  # Determine duration for which song is paused
        self.start_time += diff  # Set counter properly.
        self.paused = False

    def kill_proc(self):
        if self.proc:  # Don't call kill() on a NoneType object.
            self.proc.kill()

        self.proc = None  # Set proc to None to destroy the old process

    def go_time(self, time_continue):
        self.kill_proc()
        self.playing = False
        self.play(time_continue=time_continue)

    def rewind(self, secs):
        duration = int(time.time()) - self.start_time
        self.start_time += secs  # Compensate for the fact that we are x seconds behind where we started
        self.go_time(duration - secs)

    def forward(self, secs):
        duration = int(time.time()) - self.start_time
        self.start_time -= secs  # Compensate for the fact that we are x seconds ahead of where we started
        self.go_time(duration + secs)

    def next_track(self):
        print('SONG IS: ' + self.song)

        if self.shuffle_on:
            if self.shuffle_pool_size == 0:
                # Stop playing when the pool is empty, do not increment pool size to be lower than zero
                self.stop()
                return
            
            result = self.shuffle_pool[random.randint(0,self.shuffle_pool_size)]
            # Don't play the same song twice
            self.shuffle_pool.remove(result)
            self.shuffle_pool_size-=1
            self.stop()
            self.play_track(result)
        else:
            if self.song != 'Not playing':
                log.debug('will not add null')
                #self.up_next.append(self.song)
            if self.playing:
                self.stop()

            try:
                result = self.up_next.get_nowait() #.split('\n')  # song and album separated by LF
                print('NEXT TRACK IS', result)
                self.play_track(result)
                
            except queue.Empty:
                pass

    def play_track(self, uid):
        try:
            song_data = self.db.find_song_by_id(uid)
        except:  # stop on nonexistent song
            log.debug('failed to find song with id: %s' % uid)
            self.kill_proc()
            self.reset_metadata()
            return
        
        if len(self.up_prev) == 0 or self.up_prev[-1] != uid:  # avoid duplicate entries
            self.up_prev.append(uid)  # add to prev queue here; it is simplest this way
            
        if len(self.up_prev) > data.configuration['prev-queue-limit']:  # keep this list manageable and prevent it from covering an undue portion of the screen.
            del self.up_prev[0]
            
        self.filename = song_data[0]
        self.song = song_data[1]
        self.album = song_data[2]
        self.track = song_data[3]
        self.length = song_data[4]
        self.start_time = int(time.time())
        assert int(time.time()) - self.start_time == 0
        self.play()
        
    def old_play_track(self, name, album):
        if name == 'Not playing':
            log.debug('will not play null')
            return
        
        log.debug('AUDIO IO: Song is: ' + self.song)
        if self.song != 'Not playing' and self.album != 'Not playing' and self.album != '' and self.song != '':
            self.up_prev.append([self.song, self.album])

        if len(self.up_prev) > 15:
            del self.up_prev[0]
            
        log.debug('NAME IS ', name)
        try:
            song_data = self.db.find_songs_with_album(name, album)
        except:
            self.kill_proc()
            self.reset_metadata()
            return
        log.debug('FOUND FOLLOWING SONGS: ', song_data)
        self.filename = song_data[0]
        self.song = song_data[1]
        self.album = song_data[2]
        self.track = song_data[3]
        self.length = song_data[4]
        self.start_time = int(time.time())
        self.play()


    def _play_track(self, name):
        if name == 'Not playing':
            log.debug('will not play null')
            return
        
        log.debug('AUDIO IO: Song is: %s' % self.song)
        if self.song != 'Not playing' and self.album != 'Not playing' and self.song != '' and self.album != '':
            self.up_prev.append([self.song, self.album])

        if len(self.up_prev) > 15:
            del self.up_prev[0]
            
        log.debug('NAME IS ', name)
        try:
            song_data = self.db.find_songs(name)
        except:
            self.kill_proc()
            self.reset_metadata()
            return
        log.debug('FOUND FOLLOWING SONGS: %s' % song_data)
        self.filename = song_data[0]
        self.song = song_data[1]
        self.album = song_data[2]
        self.track = song_data[3]
        self.length = song_data[4]
        self.start_time = int(time.time())
        self.play()

    def clear_queue(self):
        tracks = list(self.up_next.queue)
        for i in tracks:
            self.up_next.get_nowait()

    def enqueue_song(self, song_id, priority=False):
        if not priority:
            self.up_next.put(song_id)
            if not self.playing:  # Don't end current song, but start as soon as we have anything to play
                self.next_track()
        else:
            self.up_next.put(song_id)

    def begin_shuffle(self, playlist=None):
        log.debug("Starting shuffle IO")
        # begin playing the specified playlist if specified, otherwise, play the entire music library
        if playlist:
            log.debug('playlist')
            # we can get an error if playlist does not exist, but that should never happen in normal usage
            self.shuffle_pool = self.db.fetch_playlist_contents(playlist)
            self.shuffle_on = True
            
        else:
            songs = self.db.fetch_songs()
            for i in songs:
                self.shuffle_pool.append(i[data.WebStereoDB.DB_SONG_ID])  # index 0 is file path

            self.shuffle_on = True

        self.shuffle_pool_size = len(self.shuffle_pool)
        self.next_track()
        log.debug("{}".format(self.shuffle_pool))

    def end_shuffle(self):
        # Turn shuffle off and delete its data
        self.shuffle_on = False
        self.shuffle_pool = []
        self.shuffle_pool_size = 0
