# Main web application in WebStereo
from flask import *
import os
import os.path
import time
import threading
import sys
import urllib.parse
import data
import audio_io
import waitress
import logging

# initialization of external modules and classes that are part of webstereo.
db = data.WebStereoDB()
player = audio_io.AudioController(db)

# initialize flask
application = Flask(__name__)
application.secret_key = os.urandom(64)

# initialize logging
logging.basicConfig(format='%(asctime)s %(levelname)s %(filename)s %(funcName)s:%(lineno)d %(name)s %(message)s')
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


@application.route('/')
def index():
    # The main page- it exists primarily to redirect to other pages. While this is technically a route, it avoids the _page convention used to specify default
    # pages for the reason that if it is the default page, accessing / while authenticated will result in an infinite redirect.
    
    # Don't bother to authenticate if the configuration doesn't require us to do so.
    if not data.configuration['authenticate']:
        session['active'] = 'YES'
    
    if 'active' in session:
        return redirect(url_for(data.configuration['default_page']))  # load the default page from configuration.
    else:
        return redirect(url_for('login_page')) # if not, show the login page


@application.route('/login', methods=['GET', 'POST'])
def login_page():
    # Don't go through the charade of logging in if authentication is disabled.
    if not data.configuration['authenticate']: return redirect(url_for('default_page'))
    
    if request.method == 'GET':
        # GET request, send page contents.
        return render_template('login.html')
    else:
        # POST request, verify password and establish user's session
        password = request.form['password']
        if data.check_valid_password(password):
            session['active'] = 'YES'  # authentication persists through browsing session.
            #session['stats'] = db.STATISTICS_MSG  # generic message about library size displayed on every page
            
            return redirect(url_for(data.configuration['default_page'])) # show albums page by default.
        else:
            return render_template('login.html', error_message='Invalid credentials')  # invalid password


@application.route('/logout')
def logout_page():
    # Log the user out; delete all data from session
    if data.configuration['authenticate']:
        session.pop('username', None)
        session.pop('active', None)
        
    return redirect('/')


@application.route('/albums')
@application.route('/albums/<int:row_width>')
def albums_page(row_width=6):
    # Display the albums, defaulting to six in each row if the URL does not specify row_width. Usually, JavaScript in the client selects the proper width
    # using the size of the browser window; the size of the album covers is set using config.json and injected into CSS using a Jinja2 template. 
    if data.configuration['authenticate'] and 'active' not in session:
            return redirect('/')  # no user is authenticated
        
    albums = db.fetch_albums()
    # tmp is added to this every row_width cycles, making it a 2-D array which is processed using nested for loops in the template to fill out an HTML table.
    # I am aware of the oceans of ink spilled in diatribe against table-based layouts, but this variable-size arrangement is what it's actually for - thus, I have no remorse for using it.
    albums_final = [] 
    tmp = []
    x = 0  # Used to map the 1-D array of albums to the 2-D array of rows.
    while True:
        # Create each row with a specified number of columns, using as many rows as necessary to fit all of the data. This infinite loop will exit at that time.
        for i in range(row_width):
            try:
                tmp.append(albums[(x * row_width) + i])
            except IndexError:
                # we know we're done when there is no more data to fetch; an IndexError will be raised at that point.
                albums_final.append(tmp)
                return render_template('albums.html', albums=albums_final, row_width=row_width)

        x += 1
        albums_final.append(tmp)  # add row to final list of albums
        tmp = []


@application.route('/songs')
def songs_page():
    # Show the songs in an HTML table. This is what <table> is for, using it for this layout is fine.
    
    # Authenticate, if the configuration stipulates that we must do so.
    if data.configuration['authenticate'] and 'active' not in session: return redirect('/')

    sql_songs = db.fetch_songs()
    songs = []
    playlists = db.fetch_all_playlist_names()  # used for the 'append to playlist' interface

    # To give the album ID to the template, data must be added to the array returned by the database (given the way the database is built from a media folder, there is no easy way - that I know of, at least - to add album IDs
    # to the songs' data structure). However, SQLite returns a tuple by default, on which one cannot run .append(). Therefore, these tuples must first be converted into lists so that .append() can be used. This done, the needed
    # data is added
    index = 0
    for i in sql_songs:
        songs.append(list(i))
        songs[index].append(db.search_albums(i[db.DB_SONG_ALBUM])[0][db.DB_ALBUM_ID])
        #log.debug(db.search_albums(i[db.DB_SONG_ALBUM])[0][db.DB_ALBUM_ID])
        index += 1

    return render_template('songs.html', songs=songs, playlists=playlists)


@application.route('/search', methods=['GET', 'POST'])
def search_page():
    # Authenticate, if the configuration stipulates that we must do so.
    if data.configuration['authenticate'] and 'active' not in session: return redirect('/')
        
    if request.method == 'GET':
        # GET request, show page
        return render_template('search.html')
    else:
        # POST request, execute search
        search_query = request.form['search-query']
        sql_results_song = db.search_in_songs(search_query)
        results_album = db.search_in_albums(search_query)
        results_song = []
        log.debug('RESULTS FOR SONG: %s' %  results_song)
        log.debug('RESULTS FOR ALBUM: %s' % results_album)

        index = 0
        for i in sql_results_song:
            results_song.append(list(i))
            results_song[index].append(db.search_albums(i[db.DB_SONG_ALBUM])[0][db.DB_ALBUM_ID])
            index += 1
            
        return render_template('search.html',
                               results=(sql_results_song or results_album),
                               results_song=results_song, size=256,
                               results_album=results_album,
                               quantity_msg='{} songs, {} albums'.format(len(sql_results_song), len(results_album)))


@application.route('/playlists', methods=['GET', 'POST'])
def playlists_page():
    # On a GET request, this shows all playlists; on a POST request, this creates a new playlist.
    if data.configuration['authenticate'] and 'active' not in session: return redirect('/')  # authentication
    
    if request.method == 'GET':
        # GET requests, show available playlists
        results = db.fetch_all_playlists()
        for i in results:
            for j in range(len(i.contents)):
                try:
                    i.contents[j] = list(i.contents[j])
                    i.contents[j].append(db.search_albums(i.contents[j][db.DB_SONG_ALBUM])[0][db.DB_ALBUM_ID])
                except IndexError:  # empty space
                    pass
                
        if results:
            return render_template('playlists.html', contents=results)
        else:
            return render_template('playlists.html', contents=[])
        
    elif request.method == 'POST':
        # POST request, create playlist
        if request.form['plname']:
            # Do nothing if name is None (not supplied)
            try:
                db.create_playlist(request.form['plname'])
            except data.DuplicateCreationError as e:  # playlist already exists
                logging.info(str(e))
                return render_template('playlists.html',
                                       error_message="Cannot create duplicate playlist",
                                       contents=[])
            
        return redirect(url_for('playlists_page'))  # takes the user back to the main playlist page, where the new playlist will now appear.


@application.route('/playlists/append/<string:_playlist>/<int:song_id>', methods=['POST'])
def append_to_playlist(_playlist, song_id):
    # Initially, this was a PUT handler in playlists_page(). However, PUT requests cannot be initiated by an HTML form. Why a protocol even exists if it can't be used that way is beyond me.
    # To work around that, the relevant logic is here, on its own URL (for it cannot be isolated by its own HTTP method).
    if data.configuration['authenticate'] and 'active' not in session: abort(403)

    playlist = urllib.parse.unquote(_playlist)
    log.debug('playlist is %s, song id is %d' % (_playlist, song_id))
    # append song to playlist
    try:
        db.append_to_playlist(playlist, song_id)
    except data.DuplicateAdditionError as e:
        logging.info(str(e))
        return render_template('playlists.html', error_message='Cannot add duplicate song')
    
    return redirect(url_for('songs_page'))  # invoked in form on songs page- send the user back there


@application.route('/playlists/delete/<int:song_id>/<string:playlist>', methods=['POST'])
def delete_from_playlist(song_id, playlist):
    # Delete all instances of a particular song from a playlist.
    if data.configuration['authenticate'] and 'active' not in session: abort(403)
    log.debug('deleting from playlist: %s' % request.url)
    try:
        db.delete_from_playlist(playlist, song_id)
    except ValueError as e:
        #  db.delete_from_playlist() calls .remove on a string. In keeping with the behavior of that function, it throws a ValueError if the value to be deleted is absent from the salient data.
        log.info(str(e))


@application.route('/nowplaying')
@application.route('/nowplaying/<string:song>')
def nowplaying_page(song=None):
    # Renders the "now playing" information at the top of the screen for the frontend. 
    # this is routinely called in backend because the logic for checking song progress and advancing to the next song is here rather than in audio_io.
    
    up_next_queue = []
    for i in list(player.up_next.queue):
        up_next_queue.append(db.find_song_by_id(i))

    up_prev_queue = []
    for i in player.up_prev:
        up_prev_queue.append(db.find_song_by_id(i))
        
    # I think these need to be separate try/except blocks so that each statement
    # gets its very own chance to fail harmlessly. This could also be solved with try/except nested inside
    # a 'finally' block, but that's almost worse.
    if song:
        log.debug('song has been specified')
        # Fetch data and return page for particular song
        song_info = db.find_songs(song)
        # Prevent '500 internal server error' if nothing is found
        if song_info:
            # This is checked here to raise RuntimeError only after the above logic has executed when run outside the context of a web request.
            if data.configuration['authentication'] and 'active' not in session: abort(403)  
            
            
            return render_template('nowplaying.html',
                                   prev_queue=up_prev_queue,
                                   next_queue=up_next_queue,
                                   song=song_info[1],
                                   album=song_info[2],
                                   track=song_info[3],
                                   length=song_info[4],
                                   time='0',
                                   fullscreen=False
                                   )
        else:
            # empty page; it shows no information because there is nothing to show.
            if 'active' not in session: abort(403)
            return render_template('nowplaying.html',
                                   next_queue=up_next_queue,
                                   song='Not playing',
                                   album='Not playing',
                                   track=0,
                                   time='0',
                                   length='0:00',
                                   fullscreen=False
                                   )
        
    else:
        # Fetch data for current song playing on the server
        if player.playing:
            if player.paused:
                log.debug('playback is paused')
                # Playback is paused. Do not increment counter during this time
                track_time = player.paused_time
            else:
                track_time = int(time.time()) - player.start_time
                
            assert track_time > -5  # enable countdowns, but warn on excessively negative values
            
            # Convert seconds to minutes and seconds, adding leading zeroes as needed.
            track_minutes = track_time // 60
            track_seconds = track_time % 60
            if track_seconds < 10:
                track_seconds_str = '0' + str(track_seconds)
            else:
                track_seconds_str = str(track_seconds)
                
            track_time_str = str(track_minutes) + ':' + track_seconds_str

            if track_time_str == player.length:
                player.next_track()

        else:
            # Not playing, thus no time available
            track_time_str = '0:00'

        # see the note on the above instance of this line for the reasoning behind its location here.
        if data.configuration['authenticate'] and 'active' not in session: abort(403)
        
        return render_template('nowplaying.html',
                               prev_queue=up_prev_queue,
                               next_queue=up_next_queue,
                               song=player.song,
                               album=player.album,
                               track=player.track,
                               length=player.length,
                               time=track_time_str,
                               fullscreen=False
                               )


@application.route('/edit-metadata/song/<int:song_id>', methods=['GET', 'POST'])
def metadata_editor_songs(song_id):
    # This, intuitively, edits the metadata on a song, both in the database and (if possible) in the audio file on disk.
    if data.configuration['authenticate'] and 'active' not in session: abort(403)
    
    if request.method == 'GET':
        result = db.find_song_by_id(song_id)
        return render_template('metadata-editor-song.html', song=result)
    else:
        # POST request, change metadata stored in database.
        # new_album is a holdover from the time before songs had unique IDs- to uniquely identify a particular song, its album had to be passed along with its name. Therefore,
        # old and new album names were stored in the form, the former using a display: none - style kludge.
        new_metadata = {
                'title': item,
                'new_title': request.form['title'],
                'album': request.form['new album'],
                'number': request.form['number'],
            }
        db.edit_song(song_id, new_metadata)

        return redirect(request.url)


@application.route('/edit-metadata/album/<int:album_id>', methods=['GET', 'POST'])
def metadata_editor_albums(album_id):
    # This edits the database entries for albums.
    if data.configuration['authenticate'] and 'active' not in session: abort(403)

    if request.method == 'GET':
        result = db.find_album_by_id(album_id)
        return render_template('metadata-editor-album.html', album=result)
    else:
        # POST request
        # Alter database entry for this album.
        new_metadata = {
                "title": request.form['title'],
                "artist": request.form['artist'],
                "genre": request.form['genre'],
                "year": request.form['year'],
            }
        db.edit_album(album_id, new_metadata)

        # change album artwork, if file is supplied. Artwork is stored as "artwork.jpg" files contained in each album's directory, not in the SQL database.
        artwork_path = db.search_albums(new_metadata['title'])[0][3]  # get location to save file
        is_allowed = lambda filename: os.path.splitext(fileitem.filename)[-1].lower() in data.configuration['allowed-artwork-extensions']  # confirm that file is permissible per config.json.

        # save the file
        log.debug('editing album artwork')
        if 'artworkupload' not in request.files:
            flash('no file part')
            log.debug('no file part')

            return redirect(request.url)

        fileitem = request.files['artworkupload']
        if fileitem.filename == '':
            flash('no file')
            log.debug('no file')

            return redirect(request.url)
        
        log.debug(os.path.splitext(fileitem.filename))
        log.debug(os.path.splitext(fileitem.filename[-1]))

        log.debug('artwork path: %s' % artwork_path)
        log.debug(fileitem.filename)
        
        if fileitem and is_allowed(fileitem.filename):
            if os.path.isfile(artwork_path):
                os.remove(artwork_path)

            fileitem.save(artwork_path)
            log.debug('file saved')

        return redirect(request.url)  # return to the metadata editor, open to the item processed above.




@application.route('/artwork/<int:album>')
@application.route('/artwork/<string:album>')
def send_artwork(album):
    # Send the image file with the cover art for the specified album
    # Authenticate, if it is enabled.
    if data.configuration['authenticate'] and 'active' not in session: abort(403)
    default_path = os.getcwd() + '/' + 'static' + '/' + 'default-artwork.jpg'

    if type(album) == int:
        f = db.fetch_album_artwork_by_id(album)
    elif album == 'none':  # special case where artwork does not exist
        return send_file(default_path)
    else:  # string, presumably
        f = db.fetch_album_artwork_by_name(album)

    if f:
        return send_file(f)
    else:
        # Some albums don't have artwork (f is None). In those cases, use the default file- the webstereo logo.
        return send_file(default_path)


@application.route('/player/<int:song_id>')
def browserplayer(song_id):
    # Allows for playing audio in the user's browser. Niceties such as up next and shuffle, however, will not work; those depend on server-side functionality.
    if data.configuration['authenticate'] and 'active' not in session: abort(403)
    s = db.find_song_by_id(song_id)
    return render_template('player.html', song=s)


@application.route('/get-audio-file/<int:song_id>')
def get_audio_file(song, album):
    # Send the audio file to the frontend, used in the browser-side player.
    if data.configuration['authenticate'] and 'active' not in session: abort(403)  # authenicate if needed.

    filepath = db.find_song_by_id(song_id)[db.DB_SONG_FILE]  # Get file path from database.
    return send_file(filepath)


@application.route('/play/song/<int:song_id>', methods=['POST'])
def play_song(song_id):
    # Play the specified song on the server 
    if data.configuration['authenticate'] and 'active' not in session: abort(403)  # authentication
    
    # Playing a particular song will disable 'shuffle all' mode if it is turned on.
    if player.shuffle_on:
        player.end_shuffle()

    player.play_track(song_id)
    return ''  # nothing for the frontend to see here


@application.route('/up-next/song/<int:song_id>', methods=['POST'])
def up_next_backend_song(song_id):
    # Adds a song to the list of songs to be played next.
    if data.configuration['authenticate'] and 'active' not in session: abort(403)  # authentication

    # As 'shuffle' mode takes control of the queue, it will be disabled when the user manually adds a song.
    if player.shuffle_on:
        player.end_shuffle()

    # Adds song to "up next" queue.
    player.enqueue_song(song_id)
    log.debug('added song: %d' % song_id)
    return ''


@application.route('/up-next/album/<int:album_id>', methods=['POST'])
def up_next_backend_album(album_id):
    if data.configuration['authenticate'] and 'active' not in session: abort(403)  # authentication

    # fetch_album_contents takes an album title, which must be extracted from the unique ID given by the URL.
    track_results = db.fetch_album_contents(db.find_album_by_id(album_id)[db.DB_ALBUM_TITLE])
    # add every song to the list for "up next."
    for i in track_results:
        player.enqueue_song(i[db.DB_SONG_ID])

    return ''


@application.route('/command/<string:parameter>', methods=['POST'])
@application.route('/command/<string:parameter>=<string:value>', methods=['POST'])
def player_command(parameter, value=''):
    # control the server-side player
    if data.configuration['authenticate'] and 'active' not in session: abort(403)  # authentication.

    if parameter == 'rew':
        player.rewind(int(value))

    elif parameter == 'fwd':
        player.forward(int(value))

    elif parameter == "pause":
        player.pause()

    elif parameter == 'resume':
        player.resume()

    elif parameter == 'next':
        player.next_track()

    elif parameter == 'purge':
        # Clear the queue
        tracks = list(player.up_next.queue)
        for i in tracks:
            player.up_next.get_nowait()

    elif parameter == 'shuffle-begin':
        log.debug("Starting shuffle with playlist %s" % value)
        player.begin_shuffle(value)

    elif parameter == 'shuffle-end':
        player.end_shuffle()
    
    return ''  # Return nothing. This is accessed via AJAX in JS, and nothing needs to be shown to the user. However, flask expects a return statement


@application.route('/stop')
def stop_playback():
    if 'active' not in session: abort(403)
    player.stop()
    player.reset_metadata()
    return ''


@application.route('/album-data/<int:album_id>')
def album_songs(album_id):
    # Display the contents of an album
    if data.configuration['authenticate'] and 'active' not in session: abort(403)
    
    try:
        # If finding the album succeeds but fetching its contents fails, something is badly wrong with the database or the structure of the library folder
        album = db.find_album_by_id(album_id)
        result = db.fetch_album_contents(album[db.DB_ALBUM_TITLE])
    except IndexError:
        abort(404)
        
    playlists = db.fetch_all_playlist_names()
    return render_template('album-contents.html',
                           album_data=album,
                           data=result,
                           playlists=playlists)


@application.context_processor
def inject_template_globals():
    # This function makes the following variables available for use in templates without having to specify in every render_template() call.
    return dict(
        db_song_file = db.DB_SONG_FILE,  # song
        db_song_title = db.DB_SONG_TITLE,
        db_song_album = db.DB_SONG_ALBUM,
        db_song_track_number = db.DB_SONG_TRACK_NUMBER,
        db_song_length = db.DB_SONG_LENGTH,
        db_song_enctype = db.DB_SONG_ENCTYPE,
        db_song_id = db.DB_SONG_ID,
        db_song_unallocated_space = db.DB_SONG_UNALLOCATED_SPACE,
        db_album_title = db.DB_ALBUM_TITLE,  # album
        db_album_artist = db.DB_ALBUM_ARTIST,
        db_album_genre = db.DB_ALBUM_GENRE,
        db_album_year = db.DB_ALBUM_YEAR,
        db_album_id = db.DB_ALBUM_ID,
        db_album_unallocated_space = db.DB_ALBUM_UNALLOCATED_SPACE,
        db_playlist_name = db.DB_PLAYLIST_NAME,  # playlist
        db_playlist_contents = db.DB_PLAYLIST_CONTENTS,
        db_playlist_modified_time = db.DB_PLAYLIST_MODIFIED_TIME,
        db_statistics = db.STATISTICS_MSG,  # misc.
        require_authentication = data.configuration['authenticate'],  # This is necessary to determine whether the nowplaying panel is shown or not.
        album_artwork_size = data.configuration['artwork_size']
        )


# Error handlers - 4XX use logging.warn, while 500 uses logging.error; the latter reflects an error in this code and is thus more important to note.

@application.errorhandler(400)
def error_400(e):
    log.warning('error 400: ' + str(e))
    log.warning(request.get_data())
    return render_template('error-400.html')


@application.errorhandler(401)
def error_401(e):
    log.warning('error 401: ' + str(e))
    log.warning(request.get_data())
    return render_template('error-401.html')


@application.errorhandler(403)
def error_403(e):
    if data.configuration['authenticate']:
        log.warning('403: ' + str(e))
        return render_template('error-403.html')
    else:
        # without authentication enabled, permissions errors are by definition impossible.
        session['active'] = 'YES'

@application.errorhandler(404)
def error_404(e):
    log.warning('error 404: ' + str(e))
    log.warning(request.url)
    return render_template('error-404.html')


@application.errorhandler(500)
def error_500(e):
    log.error(request.url)
    log.error(str(e))
    return render_template('error-500.html')


class DataUpdateThread(threading.Thread):
    # Do this to ensure that songs are rotated and the queue advances in the background.
    # This is a prime example of the perpetual quandry of whether design mistakes are worth rewriting
    # or best simply hacked around.
    def run(self):
        while True:
            try:
                nowplaying_page()
            except AttributeError:
                # Calling a routing function here, outside the normal HTTP request context, means that the flask functions for dealing
                # with web app-type things don't have the necessary prerequisites. However, the relevant code that must be executed routinely here
                # doesn't pertain to that. Trap this error so as to prevent this thread from exiting.
                pass
            except RuntimeError:
                # see above
                pass
            
            time.sleep(1) # do this every one second so that every possible time stamp is verified.


# Neither waitress.serve() nor application.run() ever return. Therefore, this thread is started before the main web app.
update_thread = DataUpdateThread()
update_thread.start()

print('Starting application on ', data.configuration['host'], ':', data.configuration['port'])

if __name__ == '__main__':
     host = data.configuration['host']
     port = data.configuration['port']
     waitress.serve(application, host=host, port=port)
