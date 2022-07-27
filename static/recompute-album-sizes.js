const album_size = parseInt(document.getElementById('retrieve-album-size').innerText);
console.assert(!Number.isNaN(album_size)); //why, Santa Claus, why must NaN != NaN?
function recomputeCoverSizes(){
    var widthOfRow = document.getElementById('albums-row-width-container').innerText;
    var clientWidth = document.documentElement.clientWidth
    var coverArtImages = document.getElementsByClassName('album-cover-display');
    var albumTableCells = document.getElementsByClassName('table-truncated');
    var coverSideLength = clientWidth / widthOfRow;
    for (var i=0;i<coverArtImages.length;i++){
	coverArtImages[i].width = coverSideLength-16;
	coverArtImages[i].height = coverSideLength-16;
    }
    console.log(coverSideLength);
    if (clientWidth < (widthOfRow * album_size)) {
	const newWidth = Math.floor(clientWidth / album_size); //force floating-point division
	window.location.replace('/albums/' + newWidth);
	console.log(newWidth);
    }
    if (Math.floor(clientWidth / album_size) > widthOfRow) {
	const newWidth = Math.floor(clientWidth / album_size);
	window.location.replace('/albums/' + newWidth);
    }
}
recomputeCoverSizes()
window.onresize = recomputeCoverSizes;
