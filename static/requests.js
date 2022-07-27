//Handle AJAX requests for frontend bits of webstereo
//(C) R. D. Ryder, 2021
var paused = false;
var currentAlbum = "Not playing";
// used to track current album and display the correct art. This was formerly done in the nowplaying page
//server-side, but that led to screen tearing on the image when the page refreshed. This, I certainly hope, fixes that problem
function playSong(songID){ //Calls backend to play song
    stopPlayback();
    //Play file remotely - send commands to backend
    var req = new XMLHttpRequest();
    req.open("POST", "/play/song/" + songID);
    req.send();
}
function togglePause(){
    var req = new XMLHttpRequest();
        if (paused) {
            req.open("POST", "/command/resume=0"); //Value doesn't matter here but it is a required parameter in app.py
            document.getElementById("play-pause").innerHTML = "PAUSE";
            paused = false;
       }
        else{
            req.open("POST", "/command/pause=0");
            document.getElementById("play-pause").innerHTML = "PLAY";
            paused = true
        }
        req.send();
}
function upNextSong(song_id){
    var req = new XMLHttpRequest();
    req.open("POST", "/up-next/song/" + song_id);
    req.send();
}
function playAlbum(name){
    var req = new XMLHttpRequest();
    console.log(name)
    req.open("POST", "/up-next/album/" + name); //actually the id//encodeURI(name));
    req.send();
}
function stopPlayback(){
    var stopReq = new XMLHttpRequest();
    stopReq.open("GET", "/stop");
    stopReq.send();
}
function rewind(secs){
        var req = new XMLHttpRequest();
        req.open("POST", "/command/rew=" + secs);
        req.send();
}
function forward(secs){
        var req = new XMLHttpRequest();
        req.open("POST", "/command/fwd=" + secs);
        req.send();
}
function next(secs){
	var req = new XMLHttpRequest();
	req.open("POST", "/command/next=0");
	req.send();
}
function purge(secs){
    //Purge queue of up next
	var req = new XMLHttpRequest();
	req.open("POST", "/command/purge=0");
	req.send();
}
function rowWidth(w){
    //Change the width of the row
    var req = new XMLHttpRequest();
    req.open("POST", "/command/rw=" + w);
    req.send();
    //console.log("set row width");
    //console.log(w)
    //document.reload(); //apply changes
    page('/albums')
}
function addToPlaylist(index, song_id) {
    var req = new XMLHttpRequest();
    var playlistSelector = document.getElementById("playlist-select" + index.toString());
    var playlist = playlistSelector.options[playlistSelector.selectedIndex].text;
    if (playlist.toLowerCase() == "select playlist") { return; }
    req.open("POST", "/playlists/append/" + encodeURI(playlist) + '/' + song_id);
    req.send();
}
function deleteFromPlaylist(plist, song_id) {
    var req = new XMLHttpRequest();
    //var playlist = document.getElementById("playlist-id" + index).innerText;
    //var song = document.getElementById("playlist-song" + index).innerText;
    //var album = document.getElementById("playlist-album" + index).innerText;
    //req.open("POST", "/playlists/delete/" + encodeURI(playlist) + '/' + encodeURI(song) + '/' + encodeURI(album));
    req.open("POST", "/playlists/delete/" + song_id + '/' + encodeURI(plist));
    console.log(song_id.toString() +  encodeURI(plist));
    req.send();
    location.reload(); //display updated playlist
}
function startShuffle(playlist){
    var req = new XMLHttpRequest();
    if (playlist == undefined) {
	var url = "/command/shuffle-begin";
    }
    else {
	var url = "/command/shuffle-begin" + '=' + encodeURI(playlist);
    }
    req.open("POST", encodeURI(url));
    req.send();
    console.log('shuffle started');
    console.log(url)
}
function endShuffle(){
    var req = new XMLHttpRequest();
    req.open("POST", "/command/shuffle-end");
    req.send();
}
function nowPlayingUpdate(){
    document.getElementById("now-playing-panel").innerHTML = this.response;
    var ca = document.getElementById("nowplaying-album-notify").innerText;
    if (ca != currentAlbum){
	document.getElementById("current-album-cover").src = "/artwork/" + ca;
    }
}
function nowPlayingLoop(){
    var npReq = new XMLHttpRequest();
    npReq.addEventListener("load", nowPlayingUpdate);
        url = "/nowplaying";
    npReq.open("GET", url);
    npReq.send();
}
nowPlayingLoop(); //Ensure this is run right at the outset.
setInterval(nowPlayingLoop, 1000);
