#!/usr/bin/env python
#
# Copyright (c) 2020 Christopher Atherton
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
#


import re
import os
import sys
import time
import math
import tempfile
import fractions
import subprocess
import datetime
import shutil
import glob
import argparse
import multiprocessing


PROGRAM_NAME = 'qtranscode'



class AVExtractor:
	CHAPTERS_TIME_RE = re.compile( r'^CHAPTER(\d+)=(\d\d):(\d\d):(\d\d)\.(\d\d\d)$' )
	CHAPTERS_TITLE_1_RE = re.compile( r'^CHAPTER(\d+)NAME=Chapter (\d+)$' )
	CHAPTERS_TITLE_2_RE = re.compile( r'^CHAPTER(\d+)NAME=(.*)$' )


	def __init__( self, path, disc_type=None, disc_title=1, chap_start=None, chap_end=None, maid=None, msid=None ):
		self.path = os.path.abspath( path )
		self.disc_type = disc_type

		self.__mplayer_input_args = ( )

		if maid is not None:
			self.__mplayer_input_args += ( '-aid', str( maid ) )
		if msid is not None:
			self.__mplayer_input_args += ( '-sid', str( msid ) )

		if disc_type == 'dvd':
			self.disc_title = disc_title
			self.__mplayer_input_args += ( '-dvd-device', path, 'dvd://' + str( disc_title ) )
		elif disc_type == 'bluray':
			self.disc_title = disc_title
			self.__mplayer_input_args += ( '-bluray-device', path, 'bluray://' + str( disc_title ) )
		else:
			self.__mplayer_input_args += ( path, )

		mplayer_probe_out = subprocess.check_output( ( 'mplayer', '-nocorrect-pts', '-vc', ',', '-vo', 'null', '-ac', 'ffmp3,', '-ao', 'null', '-endpos', '1' ) + self.__mplayer_input_args, stderr=subprocess.STDOUT ).decode()

		if disc_type != 'bluray':
			mat = re.search( r'^VIDEO:  \[?(\w+)\]?  (\d+)x(\d+) .+ (\d+\.\d+) fps', mplayer_probe_out, re.M )
			self.video_codec = mat.group( 1 )
			self.video_dimensions = ( int( mat.group( 2 ) ), int( mat.group( 3 ) ) )

			video_framerate_float = float( mat.group( 4 ) )
			if abs( math.ceil( video_framerate_float ) / 1.001 - video_framerate_float ) / video_framerate_float < 0.00001:
				self.video_framerate = fractions.Fraction( math.ceil( video_framerate_float ) * 1000, 1001 )
			else:
				self.video_framerate = fractions.Fraction( video_framerate_float )

		mat = re.search( r'^AUDIO: (\d+) Hz, (\d+) ch', mplayer_probe_out, re.M )
		self.audio_samplerate = int( mat.group( 1 ) )
		self.audio_channels = int( mat.group( 2 ) )

		mat = re.search( r'^Selected audio codec: \[(\w+)\]', mplayer_probe_out, re.M )
		self.audio_codec = mat.group( 1 )

		self.chap_start = chap_start
		self.chap_end = chap_end
		if chap_start is not None:
			chap_arg = str( chap_start )
			if chap_end is not None:
				chap_arg += '-' + str( chap_end )
			self.__mplayer_input_args += ( '-chapter', chap_arg )
		elif chap_end is not None:
			self.__mplayer_input_args += ( '-chapter', '-' + str( chap_end ) )

		self.is_matroska = os.path.splitext( path )[1].upper() == '.MKV'
		if disc_type == 'dvd':
			# Chapters
			self.has_chapters = True

			# Attachments
			self.attachment_cnt = 0

			# Subtitles
			self.has_subtitles = re.search( r'^number of subtitles on disk: [1-9]', mplayer_probe_out, re.M ) is not None
		elif self.is_matroska:
			self.__mkvmerge_probe_out = subprocess.check_output( ( 'mkvmerge', '--identify', path ), stderr=subprocess.DEVNULL ).decode()

			# Chapters
			self.has_chapters = 'Chapters: ' in self.__mkvmerge_probe_out

			# Attachments
			self.attachment_cnt = self.__mkvmerge_probe_out.count( 'Attachment ID ' )

			# Subtitles
			mat = re.search( r'^Track ID (\d+): subtitles', self.__mkvmerge_probe_out, re.M )
			if mat is not None:
				self.has_subtitles = True
				self.__mkv_subtitle_tracknum = int( mat.group( 1 ) )
			else:
				self.has_subtitles = False
		else:
			self.has_chapters = False
			self.attachment_cnt = 0
			self.has_subtitles = False


	def extract_chapters( self, filename ):
		if self.is_matroska:
			chapters = subprocess.check_output( ( 'mkvextract', 'chapters', self.path, '--simple' ), stderr=subprocess.DEVNULL ).decode()
		elif self.disc_type == 'dvd':
			chapters = subprocess.check_output( ( 'dvdxchap', '--title', str( self.disc_title ), self.path ), stderr=subprocess.DEVNULL ).decode()
		else:
			raise Exception( 'Cannot extract chapters because there are none.' )

		new_chapters = str()
		if self.chap_start is not None or self.chap_end is not None:
			if self.chap_start is not None:
				offset_index = self.chap_start - 1
				mat = re.search( r'^CHAPTER' + str( self.chap_start ).zfill( 2 ) + r'=(\d\d):(\d\d):(\d\d)\.(\d\d\d)$', chapters, re.M )
				if mat is None:
					raise Exception( 'Start chapter could not be found!' )
				offset_time = datetime.timedelta( hours=int( mat.group( 1 ) ), minutes=int( mat.group( 2 ) ), seconds=int( mat.group( 3 ) ), milliseconds=int( mat.group( 4 ) ) )
			else:
				offset_index = 0
				offset_time = datetime.timedelta()
		else:
			offset_index = 0
			offset_time = datetime.timedelta()
		for line in chapters.splitlines():
			mat = self.CHAPTERS_TIME_RE.match( line )
			if mat is not None and ( self.chap_start is None or int( mat.group( 1 ) ) >= self.chap_start ) and ( self.chap_end is None or int( mat.group( 1 ) ) <= self.chap_end ):
				new_time = datetime.timedelta( hours=int( mat.group( 2 ) ), minutes=int( mat.group( 3 ) ), seconds=int( mat.group( 4 ) ), milliseconds=int( mat.group( 5 ) ) ) - offset_time
				new_chapters += 'CHAPTER' + str( int( mat.group( 1 ) ) - offset_index ).zfill( 2 ) + '=' + str( new_time.seconds // 3600 ).zfill( 2 ) + ':' + str( new_time.seconds // 60 % 60 ).zfill( 2 ) + ':' + str( new_time.seconds % 60 ).zfill( 2 ) + '.' + str( new_time.microseconds // 1000 ).zfill( 3 ) + '\n'
			else:
				mat = self.CHAPTERS_TITLE_1_RE.match( line )
				if mat is not None and ( self.chap_start is None or int( mat.group( 1 ) ) >= self.chap_start ) and ( self.chap_end is None or int( mat.group( 1 ) ) <= self.chap_end ):
					new_chapters += 'CHAPTER' + str( int( mat.group( 1 ) ) - offset_index ).zfill( 2 ) + 'NAME=Chapter ' + str( int( mat.group( 2 ) ) - offset_index ).zfill( 2 ) + '\n'
				else:
					mat = self.CHAPTERS_TITLE_2_RE.match( line )
					if mat is not None and ( self.chap_start is None or int( mat.group( 1 ) ) >= self.chap_start ) and ( self.chap_end is None or int( mat.group( 1 ) ) <= self.chap_end ):
						new_chapters += 'CHAPTER' + str( int( mat.group( 1 ) ) - offset_index ).zfill( 2 ) + 'NAME=' + mat.group( 2 ) + '\n'

		with open( filename, 'w' ) as f:
			f.write( new_chapters )


	def extract_attachments( self, directory ):
		assert self.is_matroska
		os.mkdir( directory )
		cwd = os.getcwd()
		os.chdir( directory )
		subprocess.check_call( ( 'mkvextract', 'attachments', self.path ) + tuple( map( str, range( 1, self.attachment_cnt + 1 ) ) ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL )
		os.chdir( cwd )


	def extract_subtitles( self, filename ):
		if self.has_subtitles and self.is_matroska:
			subprocess.check_call( ( 'mkvextract', 'tracks', self.path, str( self.__mkv_subtitle_tracknum ) + ':' + filename ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL )
		elif self.has_subtitles and self.disc_type == 'dvd':
			subprocess.check_call( ( 'mencoder', '-ovc', 'copy', '-o', os.devnull, '-vobsubout', filename ) + self.__mplayer_input_args + ( '-nosound', ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL )
		else:
			raise Exception( 'Cannot extract subtitles (no subtitles)!' )


	def extract_audio( self, filename ):
		assert self.chap_start is None and self.chap_end is None
		if self.is_matroska:
			subprocess.check_call( ( 'mkvextract', 'tracks', self.path, re.search( r'^Track ID (\d+): audio \((.+)\)', self.__mkvmerge_probe_out, re.M ).group( 1 ) + ':' + filename ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL )
		else:
			subprocess.check_call( ( 'mplayer', '-dumpaudio', '-dumpfile', filename ) + self.__mplayer_input_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL )


	def decode_audio( self ):
		# TODO Expand this to other formats
		if self.is_matroska and self.chap_start is None and self.chap_end is None and self.audio_codec == 'ffflac':
			# Matroska with FLAC audio
			return ( 'mkvextract', '--redirect-output', '/dev/stderr', 'tracks', self.path, re.search( r'^Track ID (\d+): audio \((.+)\)', self.__mkvmerge_probe_out, re.M ).group( 1 ) + ':/dev/stdout' )
		else:
			return ( 'mplayer', '-quiet', '-really-quiet', '-nocorrect-pts', '-vc', 'null', '-vo', 'null', '-channels', str( self.audio_channels ), '-ao', 'pcm:fast:waveheader:file=/dev/stdout' ) + self.__mplayer_input_args


	def get_decode_video_command( self, denoise=False, pp=False, scale=None, crop=None, deint=False, ivtc=False, force_rate=None, hardsub=False ):
		filters = 'format=i420'
		if ivtc:
			if crop is None:
				filters += ',filmdint=fast=0,'
			else:
				filters += ',filmdint=fast=0/crop=' + ':'.join( map( str, crop ) )
			ofps = ( '-ofps', '24000/1001' )
		elif force_rate is not None:
			ofps = ( '-ofps', '/'.join( map( str, force_rate ) ) )
		else:
			ofps = tuple()
		if deint:
			filters += ',yadif=1'
		if crop is not None and not ivtc:
			filters += ',crop=' + ':'.join( map( str, crop ) )
		if scale is not None:
			filters += ',scale=' + ':'.join( map( str, scale ) )
		if pp:
			filters += ',pp=ha/va/dr'
		if denoise:
			filters += ',hqdn3d'
		filters += ',harddup'

		if hardsub:
			hardsub_opt = ( '-ass', )
		else:
			hardsub_opt = ( '-nosub', )

		return ( 'mencoder', '-quiet', '-really-quiet', '-sws', '9', '-vf', filters ) + ofps + ( '-ovc', 'raw', '-of', 'rawvideo', '-o', '-' ) + self.__mplayer_input_args + ( '-nosound', ) + hardsub_opt



#
# Audio encoders
#


def get_encode_aac_command( out_path, quality=None, bitrate=None ):
	assert not ( quality is not None and bitrate is not None )
	qual_args = ( )
	if shutil.which( 'fdkaac' ):
		if quality is not None:
			qual_args = ( '-m', str( round( 4.0 / 10.0 * quality + 1.0 ) ) )
		elif bitrate is not None:
			qual_args = ( '-m', '0', '-b', str( bitrate ) )
		else:
			qual_args = ( '-m', '4' )
		return ( 'fdkaac', '--ignorelength' ) + qual_args + ( '-o', out_path, '-' )
	elif shutil.which( 'neroAacEnc' ):
		if quality is not None:
			qual_args = ( '-q', str( round( quality / 10.0 ) ) )
		elif bitrate is not None:
			qual_args = ( '-br', str( bitrate ) )
		return ( 'neroAacEnc', '-ignorelength' ) + qual_args + ( '-if', '-', '-of', out_path )
	elif shutil.which( 'faac' ):
		if quality is not None:
			qual_args = ( '-q', str( round( 499.0 * quality + 10.0 ) ) )
		elif bitrate is not None:
			qual_args = ( '-b', str( bitrate ) )
		return ( 'faac', '--ignorelength' ) + qual_args + ( '-o', out_path, '-' )
	else:
		raise Exception( 'No AAC encoder!' )


def get_encode_flac_command( out_path ):
	return ( 'flac', '--ignore-chunk-sizes', '-o', out_path, '-' )


def get_encode_mp3_command( out_path, bitrate=None, quality=None ):
	assert not ( quality is not None and bitrate is not None )
	qual_args = ( )
	if quality is not None:
		qual_args = ( '-V', str( round( 10.0 - quality ) ) )
	elif bitrate is not None:
		qual_args = ( '-b', str( bitrate ) )
	return ( 'lame', ) + qual_args + ( '-', out_path )


def get_encode_opus_command( out_path, quality=None, bitrate=None ):
	if bitrate is not None:
		if quality is not None:
			qual_args = ( '--vcbr', '--bitrate', str( bitrate ) )
		else:
			qual_args = ( '--bitrate', str( bitrate ) )
	else:
		qual_args = ( '--vbr', )
	return ( 'opusenc', '--ignorelength', '--discard-comments' ) + qual_args + ( '-', out_path )


def get_encode_vorbis_command( out_path, quality=None, bitrate=None ):
	assert not ( quality is not None and bitrate is not None )
	qual_args = ( )
	if quality is not None:
		qual_args = ( '-q', str( quality ) )
	elif bitrate is not None:
		qual_args = ( '-b', str( bitrate ) )
	return ( 'oggenc', '--ignorelength', '--discard-comments' ) + qual_args + ( '-o', out_path, '-' )



#
# Video encoders
#


def get_encode_av1_command( out_path, dimensions, framerate, quality=None, bitrate=None, speed=None, cur_pass=None, stat_path=None ):
	assert not ( ( cur_pass is not None ) and ( stat_path is not None ) )
	assert ( cur_pass is None ) or ( cur_pass == 1 ) or ( cur_pass == 2 )

	qual_args = ( )
	if quality is not None:
		qual_args = ( '--rc', '0', '--qp', str( round( 63.0 - quality * 6.3 ) ) )
	elif bitrate is not None:
		qual_args = ( '--rc', '1', '--tbr', str( bitrate ) )

	pass_args = ( '-b', out_path )
	if cur_pass == 1:
		pass_args = ( '-b', os.devnull, '--irefresh-type', '2', '--pass', '1', '--stat-file', stat_path )
	elif cur_pass == 2:
		pass_args = ( '-b', out_path, '--irefresh-type', '2', '--pass', '2', '--stat-file', stat_path )

	return ( 'SvtAv1EncApp', '-i', 'stdin', '-w', str( dimensions[0] ), '-h', str( dimensions[1] ), '--fps-num', str( framerate.numerator ), '--fps-denom', str( framerate.denominator ), '--preset', str( speed ) ) + qual_args + pass_args
	#return ( 'aomenc', '--threads=' + str( multiprocessing.cpu_count() ) ) + pass_args + ( '--ivf', '--yv12', '--width=' + str( dimensions[0] ), '--height=' + str( dimensions[1] ), '--fps=' + str( framerate.numerator ) + '/' + str( framerate.denominator ) ) + qual_args + ( '-', )


def get_encode_h264_command( out_path, dimensions, framerate, sar, quality=None, bitrate=None, speed=None, cur_pass=None, stat_path=None ):
	assert not ( ( cur_pass is not None ) and ( stat_path is not None ) )
	assert ( cur_pass is None ) or ( cur_pass == 1 ) or ( cur_pass == 2 )

	qual_args = ( )
	if quality is not None:
		qual_args = ( '--crf', str( round( 51.0 - quality * 5.1 ) ) )
	elif bitrate is not None:
		qual_args = ( '--bitrate', str( bitrate ) )

	pass_args = ( '--output', out_path )
	if cur_pass == 1:
		pass_args = ( '--pass', '1', '--stats', stat_path, '--output', os.devnull )
	elif cur_pass == 2:
		pass_args = ( '--pass', '2', '--stats', stat_path, '--output', out_path )

	return ( 'x264', '--profile', 'high', '--level', '4.2', '--bluray-compat', '--muxer', 'raw', '--demuxer', 'raw', '--input-csp', 'i420', '--input-res', str( dimensions[0] ) + 'x' + str( dimensions[1] ), '--sar', str( sar.numerator ) + ':' + str( sar.denominator ), '--fps', str( framerate.numerator ) + '/' + str( framerate.denominator ), '-' ) + qual_args + pass_args


def get_encode_vp9_command( out_path, dimensions, framerate, quality=None, bitrate=None, speed=None, cur_pass=None, stat_path=None ):
	assert not ( ( cur_pass is not None ) and ( stat_path is not None ) )
	assert ( cur_pass is None ) or ( cur_pass == 1 ) or ( cur_pass == 2 )

	qual_args = ( )
	if quality is not None and bitrate is not None:
		qual_args = ( '--end-usage=cq', )
	elif quality is not None:
		qual_args = ( '--end-usage=q', )
	elif bitrate is not None:
		qual_args = ( '--end-usage=vbr', )

	if quality is not None:
		qual_args = ( '--cq-level=' + str( round( 63.0 - quality * 6.3 ) ), )
	if bitrate is not None:
		qual_args = ( '--target-bitrate=' + str( bitrate ), )

	speed_args = ( )
	if speed == 0:
		speed_args = ( "--best", )
	elif speed == 1:
		speed_args = ( "--good", )
	elif speed == 2:
		speed_args = ( "--rt" , )

	pass_args = ( '--output=' + out_path, '--passes=1' )
	if cur_pass == 1:
		pass_args = ( '--output=' + os.devnull, '--passes=2', '--pass=1', '--fpf=' + stat_path, '--auto-alt-ref=1' )
	elif cur_pass == 2:
		pass_args = ( '--output=' + out_path, '--passes=2', '--pass=2', '--fpf=' + stat_path, '--auto-alt-ref=1' )

	return ( 'vpxenc', '--codec=vp9', '--threads=' + str( multiprocessing.cpu_count() ) ) + pass_args + ( '--ivf', '--width=' + str( dimensions[0] ), '--height=' + str( dimensions[1] ), '--fps=' + str( framerate.numerator ) + '/' + str( framerate.denominator ) ) + qual_args + speed_args + ( '-', )


def get_encode_vp8_command( out_path, dimensions, framerate, quality=None, bitrate=None, speed=None, cur_pass=None, stat_path=None ):
	assert not ( ( cur_pass is not None ) and ( stat_path is not None ) )
	assert ( cur_pass is None ) or ( cur_pass == 1 ) or ( cur_pass == 2 )

	if quality is not None and bitrate is not None:
		qual_args = ( '--end-usage=cq', )
	elif quality is not None:
		qual_args = ( '--end-usage=q', )
	elif bitrate is not None:
		qual_args = ( '--end-usage=vbr', )
	else:
		qual_args = ( )

	if quality is not None:
		qual_args += ( '--cq-level=' + str( round( 63.0 - quality * 6.3 ) ), )
	if bitrate is not None:
		qual_args += ( '--target-bitrate=' + str( bitrate ), )

	speed_args = ( )
	if speed == 0:
		speed_args = ( "--best", )
	elif speed == 1:
		speed_args = ( "--good", )
	elif speed == 2:
		speed_args = ( "--rt" , )

	pass_args = ( '--output=' + out_path, '--passes=1' )
	if cur_pass == 1:
		pass_args = ( '--output=' + os.devnull, '--passes=2', '--pass=1', '--fpf=' + stat_path, '--auto-alt-ref=1' )
	elif cur_pass == 2:
		pass_args = ( '--output=' + out_path, '--passes=2', '--pass=2', '--fpf=' + stat_path, '--auto-alt-ref=1' )

	return ( 'vpxenc', '--codec=vp8', '--threads=' + str( multiprocessing.cpu_count() ) ) + pass_args + ( '--ivf', '--width=' + str( dimensions[0] ), '--height=' + str( dimensions[1] ), '--fps=' + str( framerate.numerator ) + '/' + str( framerate.denominator ) ) + qual_args + speed_args + ( '-', )



#
# Multiplexers
#


def mux_matroska_mkv( out_path, title, chapters, attachments, vid_file, vid_aspect, vid_pixaspect, vid_displaysize, aud_file, sub_file, vid_lang=None, aud_lang=None, sub_lang=None ):
	cmd = ( 'mkvmerge', )
	if title is not None:
		cmd += ( '--title', title )
	if chapters is not None:
		cmd += ( '--chapters', chapters )
	if attachments is not None:
		for i in sorted( glob.glob( os.path.join( attachments, '*' ) ) ):
			cmd += ( '--attach-file', i )
	cmd += ( '--output', out_path )
	if vid_lang is not None:
		cmd += ( '--language', '0:' + vid_lang )
	if vid_aspect is not None:
		cmd += ( '--aspect-ratio', '0:' + str( vid_aspect[0] ) + '/' + str( vid_aspect[1] ) )
	if vid_pixaspect is not None:
		cmd += ( '--aspect-ratio-factor', '0:' + str( vid_pixaspect[0] ) + '/' + str( vid_pixaspect[1] ) )
	if vid_displaysize is not None:
		cmd += ( '--display-dimensions', '0:' + str( vid_displaysize[0] ) + 'x' + str( vid_displaysize[1] ) )
	cmd += ( vid_file, )
	if aud_lang is not None:
		cmd += ( '--language', '0:' + aud_lang )
	cmd += ( aud_file, )
	if sub_lang is not None:
		cmd += ( '--language', '0:' + sub_lang )
	if sub_file is not None:
		cmd += ( sub_file, )
	subprocess.check_call( cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL )


def mux_mp4( out_path, chapters, vid_file, vid_pixaspect, aud_file, vid_lang=None, aud_lang=None ):
	cmd = ( 'MP4Box', )

	if chapters is not None:
		cmd += ( '-chap', chapters )

	vid_args = vid_file
	if vid_lang is not None:
		vid_args += ':lang=' + vid_lang
	if vid_pixaspect is not None:
		vid_args += ':par=' + str( vid_pixaspect[0] ) + ':' + str( vid_pixaspect[1] )
	cmd += ( '-add', vid_args )

	aud_args = aud_file
	if aud_lang is not None:
		aud_args += ':lang' + aud_lang
	cmd += ( '-add', aud_args )

	cmd += ( '-new', out_path )
	print( cmd )
	subprocess.check_call( cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL )



def transcode( dec_cmd, enc_cmd ):
	dec_proc = subprocess.Popen( dec_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL )
	enc_proc = subprocess.Popen( enc_cmd, stdin=dec_proc.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL )
	dec_proc.stdout.close()
	if dec_proc.wait():
		raise Exception( 'Error occurred in decoding process!' )
	if enc_proc.wait():
		raise Exception( 'Error occurred in encoding process!' )



def main( argv=None ):
	process_start_time = time.time()

	#
	# Parse command line
	#
	command_line_parser = argparse.ArgumentParser( description='convert audio/video format' )
	command_line_parser.add_argument( 'input', help='input video file', metavar='FILE' )
	command_line_parser.add_argument( '-M', '--mplayer-aid', type=int, help='set audio track (in MPlayer aid)', metavar='INT' )
	command_line_parser.add_argument( '-N', '--mplayer-sid', type=int, help='set subtitle track (in MPlayer sid)', metavar='INT' )
	command_line_parser.add_argument( '-U', '--container', default=None, type=str, choices=( 'mkv', 'webm', 'mp4' ), help='output container' )
	command_line_parser.add_argument( '-o', '--output', required=True, help='path for output file (end in .mkv or .webm or .mp4)', metavar='FILE' )

	command_line_disc_group = command_line_parser.add_argument_group( 'disc' )
	command_line_disc_mutex_group = command_line_disc_group.add_mutually_exclusive_group()
	command_line_disc_mutex_group.add_argument( '--dvd', action='store_true', help='indicate that the source is a DVD' )
	command_line_disc_mutex_group.add_argument( '--bluray', action='store_true', help='indicate that the source is a Blu-ray' )
	command_line_disc_group.add_argument( '-T', '--disc-title', default=1, type=int, help='set disc title number (default: 1)', metavar='INT' )
	command_line_disc_group.add_argument( '-Z', '--size', nargs=2, type=int, help='force input display dimensions (required for --bluray)', metavar=( 'W', 'H' ) )
	command_line_disc_group.add_argument( '-R', '--rate', nargs=2, type=int, help='force input frame rate (required for --bluray and progressive --dvd)', metavar=( 'N', 'D' ) )

	command_line_audio_group = command_line_parser.add_argument_group( 'audio' )
	command_line_audio_group.add_argument( '-a', '--audio-codec', default='opus', type=str, choices=( 'aac', 'flac', 'mp3', 'opus', 'vorbis', 'copy' ), help='audio codec' )
	command_line_audio_mode_group = command_line_audio_group.add_mutually_exclusive_group()
	command_line_audio_mode_group.add_argument( '-q', '--audio-quality', type=float, help='set output audio quality', metavar='INT' )
	command_line_audio_mode_group.add_argument( '-b', '--audio-bitrate', type=int, help='set output audio bitrate', metavar='INT' )

	command_line_video_group = command_line_parser.add_argument_group( 'video' )
	command_line_video_group.add_argument( '-v', '--video-codec', default='av1', type=str, choices=( 'av1', 'h264', 'hevc', 'vp9', 'vp8', 'copy' ), help='video codec' )
	command_line_video_mode_group = command_line_video_group.add_mutually_exclusive_group()
	command_line_video_mode_group.add_argument( '-Q', '--video-quality', type=float, help='set output video quality', metavar='INT' )
	command_line_video_mode_group.add_argument( '-B', '--video-bitrate', type=float, help='set output video bitrate', metavar='INT' )
	command_line_video_group.add_argument( '-r', '--encoder-speed', default=3, type=int, help='set video encoder speed (0..8); 0 is slower', metavar='INT' )
	command_line_video_group.add_argument( '-2', "--two-pass", action='store_true', help="use two-pass encoding" )

	command_line_metadata_group = command_line_parser.add_argument_group( 'metadata' )
	command_line_metadata_group.add_argument( '-t', '--title', help='set video title', metavar='STRING' )
	command_line_metadata_group.add_argument( '-V', '--video-language', help='set video language', metavar='LANG' )
	command_line_metadata_group.add_argument( '-A', '--audio-language', help='set audio language', metavar='LANG' )
	command_line_metadata_group.add_argument( '-S', '--subtitles-language', help='set subtitle language', metavar='LANG' )

	command_line_chapter_group = command_line_parser.add_argument_group( 'chapters' )
	command_line_chapter_group.add_argument( '-C', '--start-chapter', type=int, help='start at certain chapter', metavar='INT' )
	command_line_chapter_group.add_argument( '-E', '--end-chapter', type=int, help='stop at certain chapter', metavar='INT' )
	command_line_chapter_group.add_argument( '--no-chapters', action='store_true', help='do not include chapters from DVD/Matroska source' )

	command_line_picture_group = command_line_parser.add_argument_group( 'picture' )
	command_line_picture_group.add_argument( '-n', '--denoise', action='store_true', help='apply denoise filter' )
	command_line_picture_group.add_argument( '-p', '--post-process', action='store_true', help='perform post-processing' )
	command_line_picture_group.add_argument( '-d', '--deinterlace', action='store_true', help='perform deinterlacing' )
	command_line_picture_group.add_argument( '-i', '--ivtc', action='store_true', help='perform inverse telecine' )
	command_line_picture_group.add_argument( '-k', '--crop', nargs=4, type=int, help='crop the picture', metavar=( 'W', 'H', 'X', 'Y' ) )
	command_line_picture_group.add_argument( '-s', '--scale', nargs=2, type=int, help='scale the picture', metavar=( 'W', 'H' ) )
	command_line_picture_aspect_group = command_line_picture_group.add_mutually_exclusive_group()
	command_line_picture_aspect_group.add_argument( '-y', '--display-aspect', nargs=2, type=int, help='set the display aspect of the picture', metavar=( 'W', 'H' ) )
	command_line_picture_aspect_group.add_argument( '-x', '--pixel-aspect', nargs=2, type=int, help='set the display pixel aspect of the picture', metavar=( 'W', 'H' ) )
	command_line_picture_aspect_group.add_argument( '-z', '--display-size', nargs=2, type=int, help='set the display dimensions of the picture', metavar=( 'W', 'H' ) )
	command_line_picture_group.add_argument( '-H', '--hardsub', action='store_true', help='overlay subtitles in picture stream' )

	command_line_subtitles_group = command_line_parser.add_argument_group( 'subtitles' )
	command_line_subtitles_group.add_argument( '--no-subtitles', action='store_true', help='do not include subtitles from DVD/Matroska source' )

	command_line_other_group = command_line_parser.add_argument_group( 'other' )
	command_line_other_group.add_argument( '--no-nice', action='store_true', help='do not lower process priority' )
	command_line_other_group.add_argument( '--no-attachments', action='store_true', help='do not include attachments from Matroska source' )

	if argv is None:
		command_line = command_line_parser.parse_args()
	else:
		command_line = command_line_parser.parse_args( argv )


	# Verify command line sanity
	if command_line.bluray:
		if command_line.size is None:
			print( 'ERROR: You must manually input the size of the input for Blu-ray sources!' )
			return 1
		if command_line.rate is None:
			print( 'ERROR: You must manually input the frame rate of the input for Blu-ray sources!' )
			return 1


	# Determine output container
	output = command_line.output
	( output_prefix, output_suffix ) = os.path.splitext( output )
	if command_line.container == '.mkv':
		output = output_prefix + '.mkv'
		out_container ='mkv'
	elif command_line.container == '.webm':
		output = output_prefix + '.webm'
		out_container = 'webm'
	elif command_line.container == '.mp4':
		output = output_prefix + '.mp4'
		out_container = 'mp4'
	else:
		if output_suffix.lower() not in ( '.mkv', '.webm', '.mp4' ):
			print( 'ERROR: Output container type indeterminable!' )
			return 1
		out_container = output_suffix[1:].lower()


	# Reduce priority
	if not command_line.no_nice:
		os.nice( 10 )


	# Process
	print( '==> Processing', os.path.basename( command_line.input ), '...' )

	if command_line.dvd:
		disc_type = 'dvd'
	elif command_line.bluray:
		disc_type = 'bluray'
	else:
		disc_type = None

	if command_line.mplayer_sid is None and command_line.hardsub:
		msid = 0
	else:
		msid = command_line.mplayer_sid


	extractor = AVExtractor( command_line.input, disc_type, command_line.disc_title, command_line.start_chapter, command_line.end_chapter, command_line.mplayer_aid, msid )


	with tempfile.TemporaryDirectory( prefix=PROGRAM_NAME+'-' ) as work_dir:
		print( '==> Created work directory:', work_dir, '...' )


		# Chapters
		chapters_path = None
		if extractor.has_chapters and not command_line.no_chapters:
			if out_container == 'webm':
				print( 'WARNING: Chapters present! This is not supported in WebM container!' )
			elif out_container == 'mkv' or out_container == 'mp4':
				print( '==> Extracting chapters ...', end=str(), flush=True )
				chapters_path = os.path.join( work_dir, 'chapters' )
				extractor.extract_chapters( chapters_path )
				print( ' done.', flush=True )
			else:
				assert 0


		# Attachments
		attachments_path = None
		if extractor.attachment_cnt > 0 and not command_line.no_attachments:
			if out_container == 'webm':
				print( 'WARNING: Attachments present! This is not supported in WebM container!' )
			elif out_container == 'mp4':
				print( 'WARNING: Attachments present! This is not supported in MP4 container!' )
			elif out_container == 'mkv':
				print( '==> Extracting', extractor.attachment_cnt, 'attachment(s) ...', end=str(), flush=True )
				attachments_path = os.path.join( work_dir, 'attachments' )
				extractor.extract_attachments( attachments_path )
				print( ' done.', flush=True )
			else:
				assert 0


		# Subtitles
		subtitles_path = None
		if extractor.has_subtitles and not command_line.no_subtitles and not command_line.hardsub:
			if out_container == 'webm':
				print( 'WARNING: Subtitles present! This is not supported in WebM container!' )
			elif out_container == 'mp4':
				print( 'WARNING: Subtitles present! This is not supported in MP4 container!' )
			elif out_container == 'mkv':
				print( '==> Extracting subtitles ...', end=str(), flush=True )
				subtitles_path = os.path.join( work_dir, 'subtitles' )
				extractor.extract_subtitles( subtitles_path )
				if command_line.dvd:
					subtitles_path += '.idx'
				print( ' done.', flush=True )
			else:
				assert 0


		# Audio
		if command_line.audio_codec == 'aac':
			print( '==> Transcoding audio to AAC format ...', end=str(), flush=True )
			audio_path = os.path.join( work_dir, 'audio.mp4' )
			enc_cmd = get_encode_aac_command( audio_path, command_line.audio_quality, command_line.audio_bitrate )

		elif command_line.audio_codec == 'flac':
			print( '==> Transcoding audio to FLAC format ...', end=str(), flush=True )
			audio_path = os.path.join( work_dir, 'audio.flac' )
			enc_cmd = get_encode_flac_command( audio_path )

		elif command_line.audio_codec == 'opus':
			print( '==> Transcoding audio to Opus format ...', end=str(), flush=True )
			audio_path = os.path.join( work_dir, 'audio.opus' )
			enc_cmd = get_encode_opus_command( audio_path, command_line.audio_quality, command_line.audio_bitrate )

		elif command_line.audio_codec == 'vorbis':
			print( '==> Transcoding audio to Vorbis format ...', end=str(), flush=True )
			audio_path = os.path.join( work_dir, 'audio.ogg' )
			enc_cmd = get_encode_vorbis_command( audio_path, command_line.audio_quality, command_line.audio_bitrate )

		elif command_line.audio_codec == 'mp3':
			print( '==> Transcoding audio to MP3 format ...', end=str(), flush=True )
			audio_path = os.path.join( work_dir, 'audio.mp3' )
			enc_cmd = get_encode_mp3_command( audio_path, command_line.audio_quality, command_line.audio_bitrate )

		else:
			assert 0

		if command_line.audio_codec != 'copy':
			transcode( extractor.decode_audio(), enc_cmd )
			print( ' done.', flush=True )

		else:
			if extractor.chap_start or extractor.chap_end:
				print( 'Cannot copy audio due to chapter slicing.' )
				return 1
			print( '==> Extracting audio ...', end=str(), flush=True )
			audio_path = os.path.join( work_dir, 'audio' )
			extractor.extract_audio( audio_path )
			print( ' done.', flush=True )


		#
		# Final dimension and frame rate calculations
		#
		if command_line.scale is not None:
			final_dimensions = command_line.scale
		elif command_line.crop is not None:
			final_dimensions = command_line.crop[0:2]
		elif command_line.size is not None:
			final_dimensions = command_line.size
		else:
			final_dimensions = extractor.video_dimensions

		if command_line.display_aspect is not None:
			sar = fractions.Fraction( *command_line.display_aspect ) / fractions.Fraction( *final_dimensions )
		elif command_line.pixel_aspect is not None:
			sar = fractions.Fraction( *command_line.pixel_aspect )
		else:
			sar = fractions.Fraction( 1, 1 )

		if command_line.ivtc:
			final_rate = fractions.Fraction( 24000, 1001 )
		elif command_line.rate is not None:
			final_rate = fractions.Fraction( *command_line.rate )
		else:
			final_rate = extractor.video_framerate
		
		if command_line.deinterlace:
			final_rate *= 2


		#
		# Transcode video
		#
		dec_cmd = extractor.get_decode_video_command( command_line.denoise, command_line.post_process, command_line.scale, command_line.crop, command_line.deinterlace, command_line.ivtc, command_line.rate, command_line.hardsub )
		if command_line.video_codec == 'av1':
			video_path = os.path.join( work_dir, 'video.ivf' )
			if not command_line.two_pass:
				print( '==> Transcoding video to AV1 format ...', end=str(), flush=True )
				transcode( dec_cmd, get_encode_av1_command( video_path, final_dimensions, final_rate, command_line.video_quality, command_line.video_bitrate, command_line.encoder_speed ) )
				print( ' done.', flush=True )
			else:
				print( '==> Transcoding video to AV1 format (pass 1) ...', end=str(), flush=True )
				stat_path = os.path.join( work_dir, 'av1_stats' )
				transcode( dec_cmd, get_encode_av1_command( video_path, final_dimensions, final_rate, command_line.video_quality, command_line.video_bitrate, command_line.encoder_speed, 1, stat_path ) )
				print( ' done.', flush=True )

				print( '==> Transcoding video to AV1 format (pass 2) ...', end=str(), flush=True )
				stat_path = os.path.join( work_dir, 'av1_stats' )
				transcode( dec_cmd, get_encode_av1_command( video_path, final_dimensions, final_rate, command_line.video_quality, command_line.video_bitrate, command_line.encoder_speed, 2, stat_path ) )
				print( ' done.', flush=True )

		elif command_line.video_codec == 'h264':
			video_path = os.path.join( work_dir, 'video.264' )
			if not command_line.two_pass:
				print( '==> Transcoding video to H264 format ...', end=str(), flush=True )
				transcode( dec_cmd, get_encode_h264_command( video_path, final_dimensions, final_rate, sar, command_line.video_quality, command_line.video_bitrate, command_line.encoder_speed ) )
				print( ' done.', flush=True )
			else:
				print( '==> Transcoding video to H264 format (pass 1) ...', end=str(), flush=True )
				stat_path = os.path.join( work_dir, 'x264_stats' )
				transcode( dec_cmd, get_encode_h264_command( video_path, final_dimensions, final_rate, sar, command_line.video_quality, command_line.video_bitrate, command_line.encoder_speed, 1, stat_path ) )
				print( ' done.', flush=True )

				print( '==> Transcoding video to H264 format (pass 2) ...', end=str(), flush=True )
				stat_path = os.path.join( work_dir, 'x264_stats' )
				transcode( dec_cmd, get_encode_h264_command( video_path, final_dimensions, final_rate, sar, command_line.video_quality, command_line.video_bitrate, command_line.encoder_speed, 2, stat_path ) )
				print( ' done.', flush=True )

		elif command_line.video_codec == 'vp9':
			video_path = os.path.join( work_dir, 'video.ivf' )
			if not command_line.two_pass:
				print( '==> Transcoding video to VP9 format ...', end=str(), flush=True )
				transcode( dec_cmd, get_encode_vp9_command( video_path, final_dimensions, final_rate, command_line.video_quality, command_line.video_bitrate, command_line.encoder_speed ) )
				print( ' done.', flush=True )
			else:
				print( '==> Transcoding video to VP9 format (pass 1) ...', end=str(), flush=True )
				stat_path = os.path.join( work_dir, 'vp9_stats' )
				transcode( dec_cmd, get_encode_vp9_command( video_path, final_dimensions, final_rate, command_line.video_quality, command_line.video_bitrate, command_line.encoder_speed, 1, stat_path ) )
				print( ' done.', flush=True )

				print( '==> Transcoding video to VP9 format (pass 2) ...', end=str(), flush=True )
				stat_path = os.path.join( work_dir, 'vp9_stats' )
				transcode( dec_cmd, get_encode_vp9_command( video_path, final_dimensions, final_rate, command_line.video_quality, command_line.video_bitrate, command_line.encoder_speed, 2, stat_path ) )
				print( ' done.', flush=True )

		elif command_line.video_codec == 'vp8':
			video_path = os.path.join( work_dir, 'video.ivf' )
			if not command_line.two_pass:
				print( '==> Transcoding video to VP8 format ...', end=str(), flush=True )
				transcode( dec_cmd, get_encode_vp8_command( video_path, final_dimensions, final_rate, command_line.video_quality, command_line.video_bitrate, command_line.encoder_speed ) )
				print( ' done.', flush=True )
			else:
				print( '==> Transcoding video to VP8 format (pass 1) ...', end=str(), flush=True )
				stat_path = os.path.join( work_dir, 'vp8_stats' )
				transcode( dec_cmd, get_encode_vp8_command( video_path, final_dimensions, final_rate, command_line.video_quality, command_line.video_bitrate, command_line.encoder_speed, 1, stat_path ) )
				print( ' done.', flush=True )

				print( '==> Transcoding video to VP8 format (pass 2) ...', end=str(), flush=True )
				stat_path = os.path.join( work_dir, 'vp8_stats' )
				transcode( dec_cmd, get_encode_vp8_command( video_path, final_dimensions, final_rate, command_line.video_quality, command_line.video_bitrate, command_line.encoder_speed, 2, stat_path ) )
				print( ' done.', flush=True )

		else:
			assert 0


		# Mux
		print( '==> Multiplexing ...', end=str(), flush=True )
		if out_container == 'mkv' or out_container == 'webm':
			mux_matroska_mkv( output, command_line.title, chapters_path, attachments_path, video_path, command_line.display_aspect, command_line.pixel_aspect, command_line.display_size, audio_path, subtitles_path, command_line.video_language, command_line.audio_language, command_line.subtitles_language )
		elif out_container == 'mp4':
			mux_mp4( output, chapters_path, video_path, command_line.pixel_aspect, audio_path, command_line.video_language, command_line.audio_language )
		else:
			assert 0
		print( ' done.', flush=True )


	runtime = process_start_time - time.time()
	print( 'Done. Process took', round( runtime / 60 ), 'minutes,', round( runtime % 60 ), 'seconds.' )
	return 0


if __name__ == '__main__':
	exit_status = main()
	if exit_status:
		print( "Exiting with failure ...")
	sys.exit( exit_status )
