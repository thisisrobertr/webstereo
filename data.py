import sqlite3 as sql
import pbkdf2
import os
import os.path
import sys
import re
import threading
import subprocess
import mutagen.mp4
import mutagen.aiff
import mutagen.wave
import mutagen.flac
import mutagen.mp3
import mutagen.ogg
import contextlib
import io
import json
import time
import warnings
#import urllib.parse
#import urllib.request
import logging

from itunes_artwork import AppleDownloader, MetadataContainer

PRODUCTION = True  # Determines whether the application uses a development-grade or production-grade server
configuration = {}
DO_ARTWORK = False

#Initialize logging
logging.basicConfig(format='%(asctime)s %(levelname)s %(filename)s %(funcName)s:%(lineno)d %(name)s %(message)s')
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


pbkdf2.salt = os.urandom(32)
lock = threading.Lock()  # This is a multithreaded application, use a lock to prevent the entire program from doing segfault.
# I was previously unaware it was even possible to cause Python to segfault. Isn't this something that should only exist in the depths
# of C somewhere, a holdover from the 1980s?

# Define structure of database.

STRUCTURE_ALBUMS = '''
CREATE TABLE ALBUMS(
TITLE TEXT NOT NULL,
ARTIST TEXT NOT NULL,
GENRE TEXT NOT NULL,
ARTWORK TEXT NOT NULL,
YEAR TEXT NOT NULL,
UNIQUE_ID INTEGER NOT NULL,
ARTIST_SORTED TEXT NOT NULL)
'''

STRUCTURE_SONGS = '''
CREATE TABLE SONGS(
FILE TEXT NOT NULL,
TITLE TEXT NOT NULL,
ALBUM TEXT NOT NULL,
NUMBER TEXT NOT NULL,
LENGTH TEXT NOT NULL,
ENCTYPE TEXT NOT NULL,
UNIQUE_ID INTEGER NOT NULL,
SORTING TEXT NOT NULL)
'''

STRUCTURE_PLAYLISTS = '''
CREATE TABLE PLAYLISTS(
NAME TEXT NOT NULL,
CONTENTS TEXT NOT NULL,
MODIFIED_TIME INTEGER NOT NULL)
'''


# Set these to -1 so that the first id is 0; they are incremented before the value is returned because `return` causes the function to exit
#SQLite may have a way to do this automatically, but to keep things isolated, I do it this way. If I used SQL IDs, I don't think I would get separate
# namespaces for songs and albums.

ID_COUNTER_SONGS = -1
ID_COUNTER_ALBUMS = -1


def generate_song_id():
    global ID_COUNTER_SONGS
    ID_COUNTER_SONGS += 1
    assert not ID_COUNTER_SONGS < 0  # sanity check
    return ID_COUNTER_SONGS

def generate_album_id():
    global ID_COUNTER_ALBUMS
    ID_COUNTER_ALBUMS += 1
    assert not ID_COUNTER_ALBUMS < 0
    return ID_COUNTER_ALBUMS


class DuplicateCreationError(Exception):
    # raised when the web app tries to create a duplicate that should not exist
    pass


class DuplicateAdditionError(Exception):
    # raised when a song is added in duplicate to a playlist
    pass


class PlaylistContainer:
    # contains playlist information in named quantites, used to work around a problem with Jinja templates caused by esoteric three- and four-dimensional arrays
    def __init__(self, array, db):
        # array is presumed to be the result of webstereoDB.fetch_all_playlists()[0]
        self.title = array[0]
        self.contents = []
        self.modified_time = time.ctime(int(array[2]))
        pl_songs = array[1].split('\t')
        if len(pl_songs) > 1:
            for i in pl_songs:
                try:
                    self.contents.append(db.find_song_by_id(i))
                except IndexError:
                    pass
        else:
            self.contents = []
        log.debug('contents: %s' % self.contents)


class WebStereoDB:
    # Due in roughly equal measure to early design mistakes and the nature of SQL/SQLite's Python bindings, data is handled in lists with numbered indices rather than dicts with named keys.
    # For that reason, the following constants are used to avoid magic numbers scattered throughout the code.
    DB_SONG_FILE = 0
    DB_SONG_TITLE = 1
    DB_SONG_ALBUM = 2
    DB_SONG_TRACK_NUMBER = 3
    DB_SONG_LENGTH = 4
    DB_SONG_ENCTYPE = 5
    DB_SONG_ID = 6
    # Don't expose the sorting mechanism in the database. However, some routes in webstereo.py add data onto the arrays returned by the database using append. In the event that the structure of the SQL table is ever expanded, use
    # this variable referring to a nonexistent space so as to make that data accessible without magic-number constants in certain routes/templates and, relatedly, without requiring major refactors each time that happens
    DB_SONG_UNALLOCATED_SPACE = 8  
    
    DB_ALBUM_TITLE = 0
    DB_ALBUM_ARTIST = 1
    DB_ALBUM_GENRE = 2
    DB_ALBUM_ARTWORK = 3
    DB_ALBUM_YEAR = 4
    DB_ALBUM_ID = 5
    DB_ALBUM_UNALLOCATED_SPACE = 7 # See above note for songs
    
    DB_PLAYLIST_NAME = 0
    DB_PLAYLIST_CONTENTS = 1
    DB_PLAYLIST_MODIFIED_TIME = 2

    PAUSE_COMMIT = False  # When this is set to true, query() will not automatically commit changes to disk. This helps to optimize situations (such as in build_from()) where there are hundreds or thousands of database transactions.
    IGNORE_SORTING_CHARACTERS = ['the ', 'a ', "'", '(', '[', '...']  # Don't include the following at the beginnig of the database field that controls sorting, to avoid placing "The" under T and so forth.

    def __init__(self, dbpath=None):
        # Check for explicit database path to override config.json
        if dbpath:
            path = dbpath
        else:
            path = configuration['db-path']

        # Connect to the database and write (if it does not already exist) the structure defined above
        self.connection = sql.connect(path, check_same_thread=False)
        self.cursor = self.connection.cursor()
        try:
            self.cursor.execute(STRUCTURE_ALBUMS)
            self.cursor.execute(STRUCTURE_SONGS)
            self.cursor.execute(STRUCTURE_PLAYLISTS)
        except sql.OperationalError as e:
            sys.stderr.write(str(e))
            sys.stderr.write('\n')

        # Get statistics on DB
        albums_count = len(self.fetch_albums(silence=True))
        songs_count = len(self.fetch_songs())
        self.STATISTICS_MSG = "{} albums, {} songs".format(albums_count, songs_count)

    def query(self, command, data=None):
        # Perform an SQL query on the database. This wrapper function exists so that another SQL client/implementation could be used as a (at any rate, more of a) drop-in replacement for Python's built-in SQLite.
        if data:
            try:
                lock.acquire(True)
                cmd = self.cursor.execute(command, data)
            finally:
                lock.release()
        else:
            try:
                lock.acquire(True)
                cmd = self.cursor.execute(command)
            finally:
                lock.release()
        try:
            lock.acquire(True)
            result = cmd.fetchall()
        finally:
            lock.release()
            
        if not self.PAUSE_COMMIT:
            self.commit()

        return result

    def commit(self):
        self.connection.commit()

    def create_album(self, title, artist, genre, year, artwork=''):
        artist_sorted = artist
        for i in self.IGNORE_SORTING_CHARACTERS:
            artist_sorted = artist_sorted.removeprefix(i)
        
        self.query('INSERT INTO ALBUMS (TITLE, ARTIST, GENRE, ARTWORK, YEAR, UNIQUE_ID, ARTIST_SORTED) VALUES (?, ?, ?, ?, ?, ?, ?)', [str(title),
                                                                                                                     str(artist),
                                                                                                                     str(genre),
                                                                                                                     str(artwork),
                                                                                                                     str(year),
                                                                                                                                       generate_album_id(),
                                                                                                                                       artist_sorted
                                                                                                                     ])
        self.commit()

    def edit_album(self, album_id, data):
        self.query('UPDATE ALBUMS SET TITLE = ?, ARTIST = ?, GENRE = ?, YEAR = ? WHERE UNIQUE_ID = ?',
                   [data['title'], data['artist'], data['genre'], data['year'], album_id])
        self.commit()

    def fetch_albums(self, sort_by='ARTIST', silence=False):
        # Ideally, this would pass sort_by directly into the SQL, but that doesn't work- I'm not quite certain as to why.
        
        if sort_by == 'ARTIST':
            result = self.query('SELECT * FROM ALBUMS ORDER BY ARTIST, YEAR COLLATE NOCASE ASC')  #  [sort_by])

        elif sort_by == 'TITLE':
            result = self.query('SELECT * FROM ALBUMS ORDER BY TITLE COLLATE NOCASE ASC')

        elif sort_by == 'GENRE':
            result = self.query('SELECT * FROM ALBUMS ORDER BY GENRE COLLATE NOCASE ASC')

        elif sort_by == 'YEAR':
            result = self.query('SELECT * FROM ALBUMS ORDER BY YEAR COLLATE NOCASE ASC')

        if not silence:
            log.debug('ALBUMS: %s' % result)
        return result

    def search_albums(self, title):
        result = self.query('SELECT * FROM ALBUMS WHERE TITLE = ?', [str(title)])
        return result

    def find_album_by_id(self, uid):
        result = self.query('SELECT * FROM ALBUMS WHERE UNIQUE_ID = ?', [uid])
        return result[0]

    def fetch_album_artwork_by_name(self, name):
        return self.fetch_album_artwork_by_id(self.search_albums(name)[0][self.DB_ALBUM_ID])
    
    def fetch_album_artwork_by_id(self, uid):
        result = self.query('SELECT * FROM ALBUMS WHERE UNIQUE_ID = ?', [uid])
        if len(result) != 0:
            path = result[0][self.DB_ALBUM_ARTWORK]  # Artwork path
            if os.path.isfile(path):
                return path  # If the file exists, return its path
            else:
                # if the file does not exist, return nothing. The flask application will send default artwork.
                return None
        else:
            return None

    def fetch_album_contents(self, name):
        result = self.query('SELECT * FROM SONGS WHERE ALBUM = ? ORDER BY NUMBER', [name])
        songs = []

        return result # songs

    def search_in_albums(self, search_query):
        q = '%' + search_query + '%'
        results = self.query('SELECT * FROM ALBUMS WHERE TITLE like ?', [q])
        return results
    
    def create_song(self, file, title, album, number, length=0, enctype=''):
        # Create a separate field without leading special characters or articles to prevent placing songs with "The" under T and similar problems.
        sorted_title = title.lower()  # case-insensitive
        for i in self.IGNORE_SORTING_CHARACTERS:
            sorted_title = sorted_title.removeprefix(i)
    
        self.query('INSERT INTO SONGS (FILE, TITLE, ALBUM, NUMBER, LENGTH, ENCTYPE, UNIQUE_ID, SORTING) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', [
            str(file),
            str(title),
            str(album),
            str(number),
            str(length),
            str(enctype),
            generate_song_id(),
            sorted_title
        ])

        self.commit()

    def edit_song(self, song_id, data):
        global VERBOSE
        # Update values in database
        self.query('UPDATE SONGS SET TITLE = ?, ALBUM = ?, NUMBER = ? WHERE UNIQUE_ID = ?',
                   [data['new_title'],
                    data['album'],
                    data['number'],
                    unique_id])
        # Write metadata to the file itself on disk
        try:
            result = self.query('SELECT * FROM SONGS WHERE TITLE = ?', [data['title']])
            file_path = result[0]  # [0]  # Get first attribute of first result.
            file_type = os.path.splitext(file_path)  # Extension
            if file_type == 'm4a':
                file = mutagen.mp4.MP4(file_path)
                file['\xa9alb'] = data['album']
            elif file_type == 'flac':
                file = mutagen.flac.FLAC(file_path)
                file['TALB'] = data['album']

            self.commit()
        except Exception as e:
                log.debug('Encountered the following exception in writing metadata: %s' % str(e))
    
    def fetch_songs(self, sort_by='TITLE'):
        # See the fetch_albums function an explanation of this inelegant approach
        if sort_by == 'NUMBER':
            q = 'SELECT * FROM SONGS ORDER BY NUMBER COLLATE NOCASE ASC'
            
        elif sort_by == 'ALBUM':
            q = 'SELECT * FROM SONGS ORDER BY ALBUM COLLATE NOCASE ASC'
            
        elif sort_by == 'TITLE':
            q = 'SELECT * FROM SONGS ORDER BY SORTING COLLATE NOCASE ASC'
            
        # result = self.query('SELECT * FROM SONG S ORDER BY ?', [sort_by])
        result = self.query(q)
        
        return result

    def fetch_all_song_data(self, sort_by='NUMBER, TITLE'):
        song_query = self.query('SELECT * FROM SONGS ORDER BY ?', [sort_by])
        return song_query

    def find_songs(self, name):
        result = self.query('SELECT * FROM SONGS WHERE TITLE = ?', [name])
        return result[0]

    def find_song_by_id(self, uid):
        result = self.query('SELECT * FROM SONGS WHERE UNIQUE_ID = ?', [uid])
        
        if len(result) == 0:
            # Don't throw an IndexError if there are no results.
            return []
        
        return result[0]  # only one result, so nothing is lost here.
    
    def find_songs_with_album(self, name, album):
        result = self.query('SELECT * FROM SONGS WHERE TITLE = ? AND ALBUM = ?', [name, album])[0]  # There should only ever be one result
        return result

    def search_in_songs(self, search_query):
        q = '%' + search_query + '%'
        results = self.query('SELECT * FROM SONGS WHERE TITLE like ?', [q])
        return results
        
    def check_if_song_exists(self, file):
        # Used to determine whether to index a song - does it exist.
        result = self.query('SELECT * FROM SONGS WHERE FILE = ?', [file])
        if result:
            return True
        else:
            return False

    # Playlist contents are stored as strings with song IDs delineated by \t.
    def create_playlist(self, name):
        # creates a new playlist of name, with a delimiter string as initial contents
        (self.search_playlist(name))
        if self.search_playlist(name):
            raise DuplicateCreationError('cannot create duplicate playlist')
        else:
            self.query('INSERT INTO PLAYLISTS (NAME, CONTENTS, MODIFIED_TIME) VALUES (?, ?, ?)', [name, '', int(time.time())])
 
    def append_to_playlist(self, plist, song_id):
        log.debug('playlist is %s, song is %d' % (plist, song_id))
        contents = self.query('SELECT * from PLAYLISTS WHERE NAME = ?', [plist])[0][1] # Get present contents of playlist
        log.debug('playlist contents are %s' % contents)
        contents = contents + '\t' + str(song_id)
        self.query('UPDATE PLAYLISTS SET CONTENTS = ?, MODIFIED_TIME = ? WHERE NAME = ?', [contents, int(time.time()), plist])
        log.debug('playlist contents set to %s' % contents)
        log.debug('playlist contents accessible as %s' % self.fetch_playlist_contents(plist))
        self.commit()
        
    def delete_from_playlist(self, plist, song_id):
        contents = self.query('SELECT * FROM PLAYLISTS WHERE NAME = ?', [plist])[0][1]
        contents = contents.replace('\t{}'.format(song_id), '\t')  # preserve delineating tab character while removing value
        while '\t\t' in contents:
            contents = contents.replace('\t\t', '\t')  # If a value occurs at the end of the contents string, duplicate \t s are possible; pre-empt them here.

        # Empty song spaces will appear if this is only one tab characters; contents will create the object over which Jinja iterates.
        if contents == '\t':
            contents = ""
        self.query('UPDATE PLAYLISTS SET CONTENTS = ? WHERE NAME = ?', [contents, plist])

    def fetch_playlist_contents(self, plist):
        results = self.query('SELECT * FROM PLAYLISTS WHERE NAME = ?', [plist])[0][1].split('\t')
        return results
    
    def fetch_all_playlist_names(self):
        _results = self.query('SELECT * FROM PLAYLISTS ORDER BY MODIFIED_TIME')
        results = []
        for i in _results:
            results.append(i[0])  # only add name of each playlist

        return results
    
    def fetch_all_playlists(self):
        sql_results = self.query('SELECT * FROM PLAYLISTS ORDER BY MODIFIED_TIME')
        playlists = []
        for i in sql_results:
            playlists.append(PlaylistContainer(i, self))
            # a webstereoDB object must be passed because this object's constructor calls a non-static method on it; do that unsightly part here, safely obscured in the database
            # logic that is already unpleasant to look at.

        return playlists
    
    def search_playlist(self, name):
        results = self.query('SELECT * FROM PLAYLISTS WHERE NAME = ? ORDER BY MODIFIED_TIME', [name])
        return results
    
    def build_from(self, location):
        # Here be dragons, to borrow the time-honored adage
        build_timer = time.time()
        self.PAUSE_COMMIT = True
        log.info(location)
        artists = os.listdir(location)
        downloader = AppleDownloader(True, True, DO_ARTWORK)
        log.info('WILL REMOVE %s' % location)
        
        self.query('DROP TABLE SONGS')
        self.query('DROP TABLE ALBUMS')
        self.query(STRUCTURE_SONGS)
        self.query(STRUCTURE_ALBUMS)
        
        for artist in artists:
            log.debug('level 1: %s' % artist)
            if os.path.isdir(location + artist):
                albums = os.listdir(location + artist)
                for album in albums:
                    log.debug('level 2: %s' % album)
                    if os.path.isdir(location + artist + '/' + album):
                        songs = sorted(os.listdir(location + artist + '/' + album))
                        song_index = 1  # used to assign track numbers if all else fails.
                        for song in songs:
                            log.info(song) 
                            # MacOS X (presumably for Spotlight search indexing) creates files that have the exact same name- and thus, crucially, the same extension
                             #that are prepended with ._. Should mutagen try to read these, it dies. Do this to prevent that unpleasant outcome.
                            if song[0] == '.' and song[1] == '_':
                                os.remove(location + artist + '/' + album + '/' + song)
                                continue
                            
                            log.debug('level 3: %s' % song)
                            encoding_type = os.path.splitext(song)[1]

                            #APPLE M4A AAC IMPORT ENGINE
                            if encoding_type.__contains__('m4a'): # song.__contains__('m4a'):
                                # Song is an AAC MP4 audio file, process it accordingly
                                song_file = mutagen.mp4.MP4(location + artist + '/' + album + '/' + song)  # mutagen.M4A is depreciated, use this as a replacement
                                song_file.pprint()
                                song_album = song_file.tags['\xa9alb'][0]
                                try:
                                    song_number = song_file.tags['TRCK']
                                except KeyError:
                                    # sometimes iTunes libraries will put this before the song name. We can't remove it, because there is a chance that it's not there, and some song titles consist only of numbers
                                    # see "99" by Toto and "7" by Prince, for example.
                                    song_number = song.split(' ')[0] 
                                except TypeError:
                                    song_number = song.split(' ')[0]

                                for i in song_number:
                                    if i not in '1234567890':  # non-numerical value
                                        if song_index > 10:
                                            song_number = str(song_index)
                                        else:
                                            song_number = '0' + str(song_index)
                                            
                                        break

                                song_title = song_file.tags['\xa9nam'][0]
                                # The _ operator in Python is used to denote hidden / preliminary things but also has some semantic meaning. This seems to work, however, and I really, really do not want to refactor it.
                                _length = song_file.info.length
                                _length_minutes = int(_length / 60)
                                _length_seconds = int(_length % 60)
                                if _length_seconds < 10:
                                    # Add leading zero if needed
                                    _length_seconds = '0{}'.format(_length_seconds)
                                    
                                song_length = str(_length_minutes) + ':' + str(_length_seconds)

                                # If this album does not exist, create it
                                if not self.search_albums(song_album):
                                    album_artist = song_file.tags['\xa9ART'][0]
                                    try:
                                        album_year = song_file.tags['\xa9day'][0][
                                            0:4]  # Only use the year, omit the rest of this timestamp.
                                        album_genre = song_file.tags['\xa9gen'][0]
                                    except KeyError:
                                        album_year = '2021'  # This tag in particular has given me problems with KeyError
                                        album_genre = 'Unknown Genre'
                                    artwork_path = location + artist + '/' + album + '/' + 'artwork.jpg'
                                    try:
                                        album_cover = song_file.tags['covr']
                                        try:
                                            os.remove(artwork_path)  # Keep artwork up-to-date
                                        except FileNotFoundError:
                                            pass
                                        except UnboundLocalError:
                                            pass
                                        fbuf = open(artwork_path, 'wb')
                                        fbuf.write(album_cover[0])
                                        
                                        fbuf.flush()
                                        fbuf.close()
                                        
                                    except KeyError:
                                        log.error('could not read cover art from metadata, downloading from network')
                                        meta = MetadataContainer(album, artist)
                                        downloader.download(meta, artwork_path)

                                    self.create_album(song_album,
                                                      album_artist,
                                                      album_genre,
                                                      album_year,
                                                      artwork=artwork_path)
                                if not self.check_if_song_exists(
                                        location + artist + '/' + album + '/' + song):
                                    self.create_song(location + artist + '/' + album + '/' + song,
                                                     song_title,
                                                     song_album,
                                                     song_number,
                                                     song_length,
                                                     'MP4')
                                else:
                                    log.debug("song exists")

                            # APPLE LOSSLESS (AIFF) IMPORT CODE
                            elif encoding_type.__contains__('aif'):
                                # Song is in Apple lossless format, process its tags accordingly.
                                song_path = location + artist + '/' + album + '/' + song
                                try:
                                    song_file = mutagen.aiff.AIFF(song_path)
                                except Exception as e:
                                    log.error('failed to read :%s' % song, )
                                    continue

                                song_file.pprint()
                                try:
                                    song_album = song_file.tags['TALB'][0]
                                except KeyError:
                                    # If the album tag can't be read, use the folder name
                                    song_album = album
                                except TypeError:
                                    # NoneType returned, not subscriptable
                                    song_album = album

                                try:
                                    song_number = song_file.tags['TRCK']
                                except KeyError:
                                    song_number = song.split(' ')[0]

                                for i in song_number:
                                    if i not in '1234567890':  # non-numerical data
                                        if song_index > 10:
                                            song_number = str(song_index)
                                        else:
                                            song_number = '0' + str(song_index)
                                            
                                        break
                                    
                                try:
                                    song_title = song_file.tags['TIT2'][0]
                                except KeyError:
                                    # If the title tag can't be read, use the filename without the extensions
                                    song_title = os.path.splitext(song)[0]
                                except TypeError:
                                    # NoneType returned, not subscriptable
                                    song_album = album

                                _length = song_file.info.length
                                _length_minutes = int(_length / 60)
                                _length_seconds = int(_length % 60)
                                if _length_seconds < 10:
                                    _length_seconds = '0{}'.format(_length_seconds)

                                song_length = str(_length_minutes) + ':' + str(_length_seconds)
                                # If this album does not exist, create it
                                if not self.search_albums(song_album):
                                    artwork_path = location + artist + '/' + album + '/' + 'artwork.jpg'
                                    try:
                                        album_cover = song_file.tags['covr']
                                        try:
                                            os.remove(artwork_path)  # Keep artwork up-to-date
                                        except FileNotFoundError:
                                            pass
                                        fbuf = open(artwork_path, 'wb')
                                        fbuf.write(album_cover[0])
                                        fbuf.flush()
                                        fbuf.close()
                                        
                                    except KeyError:
                                        log.error('could not read cover art from metadata, downloading from network')
                                        if artist.lower() == 'compilations':  # Compilations directory from iTunes
                                            _artist = 'Various Artists'
                                        else:
                                            _artist = artist

                                        meta = MetadataContainer(album, _artist)
                                        downloader.download(meta, artwork_path)

                                    except TypeError:
                                        log.debug('could not read cover art from metadata, downloading from network')
                                        meta = MetadataContainer(album, artist)
                                        downloader.download(meta, artwork_path)

                                    try:
                                        album_artist = song_file.tags['TOPE'][0]
                                    except KeyError:
                                        # If the album tag can't be read, use the name of the artist directory
                                        album_artist = artist
                                        if album_artist == 'Compilations':
                                            # This is in the 'compilations' directory from iTunes. Make artist name 'Various Artists'
                                            album_artist = 'Various Artists'

                                    except TypeError:
                                        album_artist = artist
                                    try:
                                        album_genre = song_file.tags[''][0]
                                    except KeyError:
                                        # album_year = '2021'  # This tag in particular has given me problems with KeyError
                                        album_genre = 'Unknown Genre'
                                    except TypeError:
                                        # album_year = '2021'  # This tag in particular has given me problems with KeyError
                                        album_genre = 'Unknown Genre'

                                    try:
                                        album_year = song_file.tags['TYER'][0]
                                    except KeyError:
                                        album_year = '2021'
                                    except TypeError:
                                        album_year = '2021'

                                    self.create_album(song_album,
                                                      album_artist,
                                                      album_genre,
                                                      album_year,
                                                      artwork=artwork_path)
                                    
                                # if True: # self.check_if_song_exists(flac_path):
                                self.create_song(song_path,  # Add FLAC file to database
                                                 song_title,
                                                 song_album,
                                                 song_number,
                                                 song_length,
                                                 'AIFF'
                                                 )

                                # self.create_song(location + artist + '/' + album + '/' + song,
                                # song_title, song_album, song_number, song_length)

                            elif encoding_type.__contains__('flac'):
                                # Song is a FLAC file, process it accordingly.
                                try:
                                    song_file = mutagen.aiff.AIFF(
                                        location + artist + '/' + album + '/' + song)
                                except Exception:
                                    # bind to Exception because a broad array of errors can occur and none of them really matter.
                                    log.error('failed to read :%s' % song, )
                                    continue
                                
                                song_file.pprint()
                                try:
                                    song_album = song_file.tags['TALB'][0]
                                except KeyError:
                                    # If the album tag can't be read, use the folder name
                                    song_album = album
                                except TypeError:
                                    # NoneType returned, not subscriptable
                                    song_album = album

                                song_number = song.split(' ')[0]
                                if song_number[0] == '0':
                                    song_number = song_number[1]
                                    
                                try:
                                    song_title = song_file.tags['TIT2'][0]
                                except KeyError:
                                    # If the title tag can't be read, use the filename without the extensions
                                    song_title = os.path.splitext(song)[0]
                                except TypeError:
                                    # NoneType returned, not subscriptable
                                    song_album = album

                                _length = song_file.info.length
                                _length_minutes = int(_length / 60)
                                _length_seconds = int(_length % 60)
                                if _length_seconds < 10:
                                    # Add leading zero if needed
                                    _length_seconds = '0{}'.format(_length_seconds)
                                    
                                song_length = str(_length_minutes) + ':' + str(_length_seconds)
                                # If this album does not exist, create it
                                if not self.search_albums(song_album):
                                    artwork_path = location + artist + '/' + album + '/' + 'artwork.jpg'
                                    try:
                                        album_cover = song_file.tags['covr']
                                        try:
                                            os.remove(artwork_path)  # Keep artwork up-to-date
                                        except FileNotFoundError:
                                            pass
                                        fbuf = open(artwork_path, 'wb')
                                        fbuf.write(album_cover[0])
                                        
                                        fbuf.flush()
                                        fbuf.close()

                                    except KeyError:
                                        log.debug('could not read cover art from metadata, downloading from network')
                                        if artist.lower() == 'compilations':  # Compilations directory from iTunes
                                            _artist = 'Various Artists'
                                        else:
                                            _artist = artist

                                        meta = MetadataContainer(album, _artist)
                                        downloader.download(meta, artwork_path)

                                    except TypeError:
                                        log.debug('could not read cover art from metadata, downloading from network')
                                        meta = MetadataContainer(album, artist)
                                        downloader.download(meta, artwork_path)

                                    try:
                                        album_artist = song_file.tags['TOPE'][0]
                                    except KeyError:
                                        # If the album tag can't be read, use the name of the artist directory
                                        album_artist = artist
                                        if album_artist == 'Compilations':
                                            # This is in the 'compilations' directory from iTunes. Make artist name 'Various Artists'
                                            album_artist = 'Various Artists'

                                    except TypeError:
                                        album_artist = artist
                                    try:
                                        album_genre = song_file.tags[''][0]
                                    except KeyError:
                                        # album_year = '2021'  # This tag in particular has given me problems with KeyError
                                        album_genre = 'Unknown Genre'
                                    except TypeError:
                                        # album_year = '2021'  # This tag in particular has given me problems with KeyError
                                        album_genre = 'Unknown Genre'

                                    try:
                                        album_year = song_file.tags['TYER'][0]
                                    except KeyError:
                                        album_year = '2021'
                                    except TypeError:
                                        album_year = '2021'

                                    self.create_album(song_album,
                                                      album_artist,
                                                      album_genre,
                                                      album_year,
                                                      artwork=artwork_path
                                                      )

                                self.create_song(location + artist + '/' + album + '/' + song,
                                                 song_title,
                                                 song_album,
                                                 song_number,
                                                 song_length,
                                                 'FLAC'
                                                 )

                            # MPEG-3 AUDIO IMPORT CODE
                            elif encoding_type.__contains__('mp3'):
                                # Song is an MP3 file, process it accordingly
                                try:
                                    song_file = mutagen.mp3.MP3(location + artist + '/' + album + '/' + song)

                                except Exception as e:
                                    log.error('failed to read :%s' % song, )
                                    continue

                                song_file.pprint()
                                try:
                                    song_album = song_file.tags['TALB'][0]
                                except KeyError:
                                    # If the album tag can't be read, use the folder name
                                    song_album = album
                                except TypeError:
                                    # NoneType returned, not subscriptable
                                    song_album = album

                                try:
                                    song_number = song_file.tags['TRCK']
                                finally:
                                    song_number = song.split(' ')[0]

                                for i in song_number:
                                    if i not in '1234567890':  # non-numerical data is not a valid track number
                                        if song_index > 10:
                                            song_number = str(song_index)
                                        else:
                                            song_number = '0' + str(song_index)
                                        break
                                    
                                try:
                                    song_title = song_file.tags['TIT2'][0]
                                except KeyError:
                                    # If the title tag can't be read, use the filename without the extensions
                                    song_title = os.path.splitext(song)[0]
                                except TypeError:
                                    # NoneType returned, not subscriptable
                                    song_album = album

                                _length = song_file.info.length
                                _length_minutes = int(_length / 60)
                                _length_seconds = int(_length % 60)
                                if _length_seconds < 10:
                                    _length_seconds = '0%s' % str(_length_seconds)
                                    
                                song_length = str(_length_minutes) + ':' + str(_length_seconds)
                                # If this album does not exist, create it
                                if not self.search_albums(song_album):
                                    artwork_path = location + artist + '/' + album + '/' + 'artwork.jpg'
                                    try:
                                        album_cover = song_file.tags['covr']
                                        try:
                                            os.remove(artwork_path)  # Keep artwork up-to-date
                                        except FileNotFoundError:
                                            pass
                                        fbuf = open(artwork_path, 'wb')
                                        fbuf.write(album_cover[0])
                                        log.info('cover data: %s' % album_cover)
                                        fbuf.flush()
                                        fbuf.close()
                                    except KeyError:
                                        log.debug('could not read cover art from metadata, downloading from network')
                                        
                                        if artist.lower() == 'compilations':  # Compilations directory from iTunes
                                            _artist = 'Various Artists'
                                        else:
                                            _artist = artist

                                        meta = MetadataContainer(album, _artist)
                                        downloader.download(meta, artwork_path)

                                    except TypeError:
                                        log.info('could not read cover art from metadata, downloading from network')
                                        meta = MetadataContainer(album, artist)
                                        downloader.download(meta, artwork_path)

                                    try:
                                        album_artist = song_file.tags['TOPE'][0]
                                    except KeyError:
                                        # If the album tag can't be read, use the name of the artist directory
                                        album_artist = artist
                                        if album_artist == 'Compilations':
                                            # This is in the 'compilations' directory from iTunes. Make artist name 'Various Artists'
                                            album_artist = 'Various Artists'

                                    except TypeError:
                                        album_artist = artist
                                    try:
                                        album_genre = song_file.tags[''][0]
                                    except KeyError:
                                        # album_year = '2021'  # This tag in particular has given me problems with KeyError
                                        album_genre = 'Unknown Genre'
                                    except TypeError:
                                        # album_year = '2021'  # This tag in particular has given me problems with KeyError
                                        album_genre = 'Unknown Genre'

                                    try:
                                        album_year = song_file.tags['TYER'][0]
                                    except KeyError:
                                        album_year = '2021'
                                    except TypeError:
                                        album_year = '2021'

                                    self.create_album(song_album,
                                                      album_artist,
                                                      album_genre,
                                                      album_year,
                                                      artwork=artwork_path
                                                      )

                                self.create_song(location + artist + '/' + album + '/' + song,
                                                 song_title,
                                                 song_album,
                                                 song_number,
                                                 song_length,
                                                 'MP3'
                                                 )

                            # WAVE AUDIO IMPORT CODE
                            elif encoding_type.__contains__('wav'):
                                # WAV files don't have portable metadata. Just use file names etc.
                                song_title = os.path.splitext(song)[0]
                                song_number = song.split(' ')[0]
                                for i in song_number:  # non-numerical value
                                    if i not in '1234567890':
                                        if song_index > 10:
                                            song_number = str(song_index)
                                        else:
                                            song_number = '0' + str(song_index)
                                        break
                                    
                                song_album = album
                                song_file = mutagen.wave.WAVE(
                                    location + artist + '/' + album + '/' + song)

                                # Control for track numbers that contain leading zeroes
                                if song_number[0] == '0' and len(song_number) > 1:
                                    song_number = song_number[1]

                                _length = song_file.info.length
                                _length_minutes = int(_length / 60)
                                _length_seconds = int(_length % 60)
                                if _length_seconds < 10:
                                    # Append leading zero if required
                                    _length_seconds = '0{}'.format(_length_seconds)
                                    
                                song_length = str(_length_minutes) + ':' + str(_length_seconds)

                                if not self.search_albums(song_album):
                                    artwork_path = location + artist + '/' + album + '/' + 'artwork.jpg'
                                    try:
                                        album_cover = song_file.tags['covr']
                                        try:
                                            os.remove(artwork_path)  # Keep artwork up-to-date
                                        except FileNotFoundError:
                                            pass
                                        fbuf = open(artwork_path, 'wb')
                                        fbuf.write(album_cover[0])
                                        log.info('cover data: %s' % album_cover)
                                        fbuf.flush()
                                        fbuf.close()
                                    except KeyError:
                                        log.debug('could not read cover art from metadata, downloading from network')
                                        meta = MetadataContainer(album, artist)
                                        downloader.download(meta, artwork_path)

                                    except TypeError:
                                        log.info('could not read cover art from metadata, downloading from network')
                                        meta = MetadataContainer(album, artist)
                                        downloader.download(meta, artwork_path)

                                    album_title = album
                                    album_year = '2021'
                                    album_genre = 'Unknown Genre'
                                    album_artist = artist
                                    self.create_album(song_album,
                                                      album_artist,
                                                      album_genre,
                                                      album_year,
                                                      artwork=artwork_path
                                                      )

                                self.create_song(location + artist + '/' + album + '/' + song,
                                                 song_title,
                                                 song_album,
                                                 song_number,
                                                 song_length,
                                                 'WAVE'
                                                 )
                                
                            # OGG CONTAINER IMPORT CODE
                            elif encoding_type.__contains__('ogg'):
                                # Song is an  OGG audio file, process it accordingly
                                song_file = mutagen.ogg.OggFileType(
                                    location + artist + '/' + album + '/' + song)
                                song_file.pprint()
                                song_album = song_file.tags['\xa9alb'][0]
                                try:
                                    song_number = song_file.tags['TRCK']
                                except KeyError:
                                    song_number = song.split(' ')[0]

                                for i in song_number:
                                    if i not in '1234567890':
                                        if song_index > 10:
                                            song_number = str(song_index)
                                        else:
                                            song_number = '0' + str(song_index)
                                        break
                                
                                song_title = song_file.tags['\xa9nam'][0]
                                _length = song_file.info.length
                                _length_minutes = int(_length / 60)
                                _length_seconds = int(_length % 60)
                                if _length_seconds < 10:
                                    # Add leading zero if needed
                                    _length_seconds = '0{}'.format(_length_seconds)
                                    
                                song_length = str(_length_minutes) + ':' + str(_length_seconds)

                                # If this album does not exist, create it
                                if not self.search_albums(song_album):
                                    album_artist = song_file.tags['\xa9ART'][0]
                                    try:
                                        album_year = song_file.tags['\xa9day'][0][
                                            0:4]  # Only use the year, omit the rest of this timestamp.
                                        album_genre = song_file.tags['\xa9gen'][0]
                                    except KeyError:
                                        album_year = '2021'  # This tag in particular has given me problems with KeyError
                                        album_genre = 'Unknown Genre'
                                    artwork_path = location + artist + '/' + album + '/' + 'artwork.jpg'
                                    try:
                                        album_cover = song_file.tags['covr']
                                        try:
                                            os.remove(artwork_path)  # Keep artwork up-to-date
                                        except FileNotFoundError:
                                            pass
                                        except UnboundLocalError:
                                            pass
                                        fbuf = open(artwork_path, 'wb')
                                        fbuf.write(album_cover[0])

                                        fbuf.flush()
                                        fbuf.close()
                                        
                                    except KeyError:
                                        log.error('could not read cover art from metadata, downloading from network')
                                        meta = MetadataContainer(album, artist)
                                        downloader.download(meta, artwork_path)

                                    self.create_album(song_album,
                                                      album_artist,
                                                      album_genre,
                                                      album_year,
                                                      artwork=artwork_path)
                                if not self.check_if_song_exists(
                                        location + artist + '/' + album + '/' + song):
                                    self.create_song(location + artist + '/' + album + '/' + song,
                                                     song_title,
                                                     song_album,
                                                     song_number,
                                                     song_length,
                                                     'OGG')
                                else:
                                    log.debug("song exists")

                                # end conditional which checks whether the album exists (col 32)
                            # end conditional to select codec (col 28)
                            song_index+= 1
                        #end for loop for songs (col 24)
                    # end conditional to check whether the album is a directory (col 20)
                # end for loop for albums (col 16)
            # end conditional confirming that artist is a directory (col 12)
        # end for loop for artists (col 8)
        self.PAUSE_COMMIT = False
        self.commit()

        build_time = int(time.time() - build_timer)
        print('Done in: ', build_time / 3600, ':', (build_time % 3600) / 60, ':', build_time % 60)

def check_valid_password(password):
    #global PASSWORD

    if pbkdf2.crypt(password, VALID_PASSWORD) == VALID_PASSWORD:
        log.info('valid password')
        return True
    else:
        log.info('invalid password')
        return False

CONFIGURATION_TEMPLATE = {
    "authenticate": True,
    "host": "0.0.0.0",
    "port": 8000,
    "allowed-artwork-extensions":[
        ".jpg",
        ".png",
        ".jp2",
        ".tif",
        ".tiff",
        ".bmp",
        ".gif",
        ".xbm"
    ],
    "library-path": "/path/somewhere",
    "db-path": "media.db",
    "artwork_size": 200,
    "default_page": "songs_page",
    "prev-queue-limit": 10,
    "DO NOT EDIT BELOW THIS LINE": True,
    "password-hash": ""
}

def write_configuration_file():
    with open('config.json', 'w') as f:
        json.dump(configuration, f, indent=2)

def reset_configuration_file():
    if os.path.exists('config.json'):
        os.remove('config.json')
        
    with open('config.json', 'x') as f:
         json.dump(CONFIGURATION_TEMPLATE, f, indent=2)
         
    #configuration = CONFIGURATION_TEMPLATE


# Initialize module and application
if os.path.exists('config.json'):
    with open('config.json', 'r') as f:
        configuration = json.loads(f.read())
else:
   reset_configuration_file()

VALID_PASSWORD = configuration['password-hash']
DB_PATH = configuration['db-path']

USAGE='''
Usage: python3 data.py [OPTIONS]

-p, --set-password:
        Set a new password. A prompt of '?' is provided, echo will be turned off if your termial supports it.
        If not, a warning will be shown and the characters will be exposed as they are entered. This is stored
        in password-hash.txt as a pbkdf2-encoded hash with a 32-bit salt.
-l, --library-path [PATH]
        This option will change the path to the audio library in config.json. Changes will not be applied until the
        database is rebuilt with -b.
-n, --network [HOST] [PORT]
        This changes the host and port specified in config.json.
-c, --configure
        This creates a new configuration file with default parameters
-f --db-file
        This sets db-file in config.json, changing the location of the database created with -b. Please note that while
        this will not delete the old database, it will mean that its contents will not be displayed; you may have to rebuild
        the database
-b, --build-db:
        This will build the database at the location specified by the 'library-path' option in config.json;
        artist, album and song names will be displayed as they are added. webstereo was designed to replace iTunes,
        and the model of its library reflects that: it expects that you have audio files inside folders for
        each album, inside folders for each artist. Cover art will be fetched from the iTunes store. Currently,
        the following audio formats are supported: MP3, MP4 (AAC/M4A), AIFF, WAV (without portable metadata),
        and FLAC.
-a, --artwork
        This option, when used with -b, will enable the downloading of artwork from iTunes.

All of the above commands assume that you are in the same directory as the application file. If that is not the case, unpleasant side effects may result.

(C) 2022 Robert Ryder
'''

if __name__ == '__main__':
    db = WebStereoDB(DB_PATH)
    if len(sys.argv) < 2:
        print('Unsupported usage. Use with --usage or --help to view usage.')
        raise SystemExit

    try:
        if sys.argv[1] in ['--set-password', '-p']:
            if os.environ['SHELL'] not in ['/bin/sh', '/bin/bash', '/bin/zsh']:  # I don't know whether the stty trick works for shells other than those which are here enumerated.
                sys.stderr.write('Possibly unable to disable echo. Password characters may be shown as plaintext in the terminal.')
                raise SystemExit
            os.system('stty -echo')  # This doesn't work in zsh.
            pw = input('?')
            configuration['password-hash'] = pbkdf2.crypt(pw)
            with open('config.json', 'w') as f:
                json.dump(configuration, f, indent=4)

            print('done')
            os.system('stty echo')  # restore terminal state.

        elif sys.argv[1] in ['--library-path', '-l']:
            configuration['library-path'] = sys.argv[2]

        elif sys.argv[1] in ['-n', '--network']:
            if sys.argv[2].count('.') == 3:  # Valid X.X.X.X IP address
                configuration['host'] = sys.argv[2]
            else:
                raise ValueError
            configuration['port'] = int(sys.argv[3])

        elif sys.argv[1] in ['-c', '--configure']:
            #os.remove('config.json')
            reset_configuration_file()
            
        elif sys.argv[1] in ['--db-file', '-f']:
            configuration['db-path'] = sys.argv[2]
        
        elif sys.argv[1] in ['--build-db', '-b']:
            if '-a' in sys.argv:
                DO_ARTWORK = True  # download artwork

            print('building database')
            db.build_from(configuration['library-path'])

        elif sys.argv[1] == '--usage' or sys.argv[1] == '--help' or sys.argv[1] == '-h':
            # Print usage message
            print(USAGE)

        else:
            raise ValueError

        write_configuration_file()
        
    except (IndexError, ValueError):
        # Either an unknown option (ValueError) or missing parameters (IndexError)
        print('Malformed arguments. See usage with --help')

# This must run after checking for command-line switches so that -p has a chance to run before this preempts it.
if configuration['authenticate'] and VALID_PASSWORD == '':
    sys.stderr.write('Authentication is enabled but no password is set. Please set a password using this script with the -p option.')
    raise SystemExit
