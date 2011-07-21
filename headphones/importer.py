from lib.pyItunes import *
import time
import os
from lib.beets.mediafile import MediaFile

import headphones
from headphones import logger, helpers, db, mb

various_artists_mbid = '89ad4ac3-39f7-470e-963a-56509c546377'

def scanMusic(dir=None):

	if not dir:
		dir = headphones.MUSIC_DIR

	results = []
	
	for r,d,f in os.walk(dir):
		for files in f:
			if any(files.endswith(x) for x in (".mp3", ".flac", ".aac", ".ogg", ".ape")):
				results.append(os.path.join(r, files))
				
	logger.info(u'%i music files found. Reading metadata....' % len(results))
	
	if results:
	
		myDB = db.DBConnection()
		myDB.action('''DELETE from have''')
	
		for song in results:
			try:
				f = MediaFile(song)
				#logger.debug('Reading: %s' % song.decode('UTF-8'))
			except:
				logger.warn('Could not read file: %s' % song.decode('UTF-8'))
			else:	
				if f.albumartist:
					artist = f.albumartist
				elif f.artist:
					artist = f.artist
				else:
					continue
				
				myDB.action('INSERT INTO have VALUES( ?, ?, ?, ?, ?, ?, ?, ?, ?)', [artist, f.album, f.track, f.title, f.length, f.bitrate, f.genre, f.date, f.mb_trackid])
				
		# Get the average bitrate if the option is selected
		if headphones.DETECT_BITRATE:
			try:
				avgbitrate = myDB.action("SELECT AVG(BitRate) FROM have").fetchone()[0]
				headphones.PREFERRED_BITRATE = int(avgbitrate)/1000
				
			except Exception, e:
				logger.error('Error detecting preferred bitrate:' + str(e))
			
		artistlist = myDB.action('SELECT DISTINCT ArtistName FROM have').fetchall()
		logger.info(u"Preparing to import %i artists" % len(artistlist))
		
		artistlist_to_mbids(artistlist)

def itunesImport(pathtoxml):
	
	if os.path.splitext(pathtoxml)[1] == '.xml':
		logger.info(u"Loading xml file from"+ pathtoxml)
		pl = XMLLibraryParser(pathtoxml)
		l = Library(pl.dictionary)
		lst = []
		for song in l.songs:
			lst.append(song.artist)
		rawlist = {}.fromkeys(lst).keys()
		artistlist = [f for f in rawlist if f != None]
	
	else:
		rawlist = os.listdir(pathtoxml)
		logger.info(u"Loading artists from directory:" +pathtoxml)
		exclude = ['.ds_store', 'various artists', 'untitled folder', 'va']
		artistlist = [f for f in rawlist if f.lower() not in exclude]
		
	logger.info('Starting directory/xml import...')
	artistlist_to_mbids(artistlist)
	
		
def is_exists(artistid):

	myDB = db.DBConnection()
	
	# See if the artist is already in the database
	artistlist = myDB.select('SELECT ArtistID, ArtistName from artists WHERE ArtistID=?', [artistid])
	
	if any(artistid in x for x in artistlist):
		logger.info(artistlist[0][1] + u" is already in the database, skipping")
		return True
	else:
		return False


def artistlist_to_mbids(artistlist):

	for artist in artistlist:
	
		results = mb.findArtist(artist['ArtistName'], limit=1)
		
		if not results:
			continue
		
		try:	
			artistid = results[0]['id']
		
		except IndexError:
			logger.info('MusicBrainz query turned up no matches for: %s' % artist)
			continue
		
		if artistid != various_artists_mbid and not is_exists(artistid):
			addArtisttoDB(artistid)


def addArtisttoDB(artistid):
	
	# Can't add various artists - throws an error from MB
	if artistid == various_artists_mbid:
		logger.warn('Cannot import Various Artists.')
		return
		
	myDB = db.DBConnection()
		
	artist = mb.getArtist(artistid)
	
	if not artist:
		return
	
	if artist['artist_name'].startswith('The '):
		sortname = artist['artist_name'][4:]
	else:
		sortname = artist['artist_name']
		

	logger.info(u"Now adding/updating: " + artist['artist_name'])
	controlValueDict = {"ArtistID": 	artistid}
	newValueDict = {"ArtistName": 		artist['artist_name'],
					"ArtistSortName": 	sortname,
					"DateAdded": 		helpers.today(),
					"Status": 			"Loading"}
	
	myDB.upsert("artists", newValueDict, controlValueDict)

	for rg in artist['releasegroups']:
		
		rgid = rg['id']
		
		# check if the album already exists
		rg_exists = myDB.select("SELECT * from albums WHERE AlbumID=?", [rg['id']])
					
		try:	
			release_dict = mb.getReleaseGroup(rgid)
		except Exception, e:
			logger.info('Unable to get release information for %s - it may not be a valid release group' % rg['title'])
			continue
			
		if not release_dict:
			continue
		
		release = mb.getRelease(release_dict['releaseid'])
		
		if not release:
			logger.warn('Unable to get release information for %s. Skipping for now.' % rg['title'])
			continue
	
		logger.info(u"Now adding/updating album: " + rg['title'])
		controlValueDict = {"AlbumID": 	rg['id']}
		
		if len(rg_exists):
		
			newValueDict = {"AlbumASIN":		release['asin'],
							"ReleaseDate":		release_dict['releasedate'],
							}
		
		else:
		
			newValueDict = {"ArtistID":			artistid,
							"ArtistName": 		artist['artist_name'],
							"AlbumTitle":		rg['title'],
							"AlbumASIN":		release['asin'],
							"ReleaseDate":		release_dict['releasedate'],
							"DateAdded":		helpers.today(),
							}
							
			if release['date'] > helpers.today():
				newValueDict['Status'] = "Wanted"
			else:
				newValueDict['Status'] = "Skipped"
		
		myDB.upsert("albums", newValueDict, controlValueDict)
		
		# I changed the albumid from releaseid -> rgid, so might need to delete albums that have a releaseid
		myDB.action('DELETE from albums WHERE AlbumID=?', [release['id']])	
						
		for track in release['tracks']:
		
			controlValueDict = {"TrackID": 	track['id'],
								"AlbumID":	rg['id']}
			newValueDict = {"ArtistID":		artistid,
						"ArtistName": 		artist['artist_name'],
						"AlbumTitle":		rg['title'],
						"AlbumASIN":		release['asin'],
						"TrackTitle":		track['title'],
						"TrackDuration":	track['duration'],
						}
		
			myDB.upsert("tracks", newValueDict, controlValueDict)
			
	controlValueDict = {"ArtistID": 	artistid}
	newValueDict = {"Status": 			"Active"}
	
	myDB.upsert("artists", newValueDict, controlValueDict)