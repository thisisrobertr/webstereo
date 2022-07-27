var isLoadingAlbums = false; //Flags whether albums are being loaded. This is used to communicate across functions and ensure that the subtly different JavaScript code for this particular page is executed properly
function recomputeCoverSizes(){
    var rowWidth = document.getElementById("row-width-container").innerText
    //Used to determine the appropriate size at which to render album covers.
      //This must be an inline script because it relies on formatting in the Flask template.render() call. I hate this too.
    var coverArtImages = document.getElementsByTagName('img');
    var coverSideLength = document.documentElement.clientWidth / rowWidth;
    for (var i=0;i<coverArtImages.length;i++){
	coverArtImages[i].width = coverSideLength;
	coverArtImages[i].height = coverSideLength;
    }

}

/*function writePage() {
    document.getElementById("content").innerHTML = this.response; //Set page to show HTML data
    if (isLoadingAlbums){
	//This must be done here
	recomputeCoverSizes();
	isLoadingAlbums = false;
    }
}
function page(loc){
    console.log(loc)
    document.getElementById("content").innerHTML = '<h1>LOADING<h1>';
    //Do *something* immediately to avoid the feeling of delayed reaction.
    //See the bit about the Netscape animation.
    //Fetch data to populate "content" div with
    var Req = new XMLHttpRequest();
    Req.addEventListener("load", writePage);
    Req.open("GET", loc);
    Req.send();
    //These are hardcoded. Yes, this sucks. Yes, I should do this in some less-braindead fashion. Yes, I'm much too lazy to bother with that.
    //Yes, this probably makes me a bad programmer. Yes, I know.
    if (loc.includes('album')) {
	document.title = "webstereo | Albums";
	recomputeCoverSizes();
	isLoadingAlbums = true; //we're doing it now.
	window.onresize = recomputeCoverSizes;
    }
    else if (loc.includes('song')){
	document.title = "webstereo | Songs";
	window.onresize = () => {}; //Empty event handler
    }
    else {
	document.title = "webstereo";
	window.onresize = () => {}; //Empty event handler
    }
    }*/

