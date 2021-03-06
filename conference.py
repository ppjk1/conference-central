#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime
from functools import wraps

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import StringMessage
from models import BooleanMessage
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import TeeShirtSize
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import Speaker
from models import SpeakerForm
from models import SpeakerForms
from models import Session
from models import SessionForm
from models import SessionForms
from models import SessionMiniHardForm
from models import TypeOfSession

from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

from utils import getUserId
from utils import getSeconds
from utils import getTimeString

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
MEMCACHE_FEATURED_SPEAKER_KEY = "FEATURED_SPEAKER_"
FEATURED_SPEAKER_TPL = ('Featured speaker: %s\nSessions: %s')

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": ["Default", "Topic"],
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS = {
         'CITY': 'city',
         'TOPIC': 'topics',
         'MONTH': 'month',
         'MAX_ATTENDEES': 'maxAttendees',
         }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

CONF_BY_ORGANIZER_GET = endpoints.ResourceContainer(
    message_types.VoidMessage,
    organizer=messages.StringField(1),
)

SESS_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

SESS_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1),
)

SESS_TYPE_GET = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    typeOfSession=messages.StringField(2),
)

SESS_SPEAKER_GET = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSpeakerKey=messages.StringField(1),
)

SESS_WISHLIST_POST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1),
)

SESS_WISHLIST_GET = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

SESS_HARD_QUERY_POST = endpoints.ResourceContainer(
    SessionMiniHardForm,
    websafeConferenceKey=messages.StringField(1),
)

FEATURED_SPEAKER_GET = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)


# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
               allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID,
                                   ANDROID_CLIENT_ID, IOS_CLIENT_ID],
               scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(
                        TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf

    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if
        non-existent."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required.')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key=p_key,
                displayName=user.nickname(),
                mainEmail=user.email(),
                teeShirtSize=str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile

    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)

    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()

    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf

    def _createConferenceObject(self, request):
        """Create or update Conference object, returning
        ConferenceForm/request."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required.')

        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException(
                "Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name)
            for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing
        # (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects;
        # set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(
                data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(
                data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email')
        return request

    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required.')

        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name)
            for field in request.all_fields()}

        # update existing conference
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)

    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)

    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        wsck = request.websafeConferenceKey
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by logged in user."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required.')

        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(
                conf, getattr(prof, 'displayName')) for conf in confs]
        )

    @endpoints.method(CONF_BY_ORGANIZER_GET, ConferenceForms,
            path='getConferencesByOrganizer/{organizer}',
            name='getConferencesByOrganizer')
    def getConferencesByOrganizer(self, request):
        """Return conferences created by organizer."""
        q = Profile.query()
        q = q.filter(Profile.displayName == request.organizer)
        prof = q.get()

        if not prof:
            raise endpoints.BadRequestException('Organizer not found.')

        q = Conference.query()
        q = q.filter(Conference.organizerUserId == prof.key.id())
        confs = q.fetch()

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(
                conf, getattr(prof, 'displayName')) for conf in confs]
        )

    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"],
                filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q

    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name)
                for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException(
                    "Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException(
                        "Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)

    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId))
            for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(
                    conf, names[conf.organizerUserId]) for conf in conferences]
        )

# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser()

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser()
        conf_keys = [ndb.Key(urlsafe=wsck)
            for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId)
            for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[
            self._copyConferenceToForm(conf, names[conf.organizerUserId])
            for conf in conferences]
        )

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)

# - - - Speakers - - - - - - - - - - - - - - - - - - - -

    def _copySpeakerToForm(self, speaker):
        """Copy fields from Speaker to SpeakerForm."""
        sf = SpeakerForm()
        for field in sf.all_fields():
            if hasattr(speaker, field.name):
                setattr(sf, field.name, getattr(speaker, field.name))
            elif field.name == 'websafeKey':
                setattr(sf, field.name, speaker.key.urlsafe())
        sf.check_initialized()
        return sf

    def _createSpeakerObject(self, request):
        """Create Speaker object, returning SpeakerForm request."""
        # User must be authenticated to create Speaker
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required.')

        # 'name' is a required field
        if not request.name:
            raise endpoints.BadRequestException(
                "Speaker 'name' field required")

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: str(getattr(request, field.name))
                for field in request.all_fields()}
        del data['websafeKey']

        # create Speaker
        Speaker(**data).put()

        return request

    @endpoints.method(SpeakerForm, SpeakerForm,
            path="speaker", http_method='POST', name='createSpeaker')
    def createSpeaker(self, request):
        """Create new speaker."""
        return self._createSpeakerObject(request)

    @endpoints.method(message_types.VoidMessage, SpeakerForms,
            path='speakers', name='getSpeakers')
    def getSpeakers(self, request):
        """Get all speakers."""
        speakers = Speaker.query().order(Speaker.name)

        # return individual SpeakerForm object per Speaker
        return SpeakerForms(
            speakers=[self._copySpeakerToForm(s) for s in speakers]
        )

# - - - Sessions - - - - - - - - - - - - - - - - - - - - - -

    def _copySessionToForm(self, sess):
        """Copy relevant fields from Session to SessionForm."""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(sess, field.name):
                # Convert date to string
                if field.name == 'date':
                    setattr(sf, field.name, str(getattr(sess, field.name)))
                # Convert integer seconds to time string
                elif field.name == 'startTime' and getattr(sess, field.name) != None:
                    setattr(sf, field.name,
                            getTimeString(getattr(sess, field.name)))
                # Convert string to ENUM
                elif field.name == 'typeOfSession':
                    setattr(sf, field.name, getattr(
                        TypeOfSession, getattr(sess, field.name)))
                # Just copy the rest
                else:
                    setattr(sf, field.name, getattr(sess, field.name))
            elif field.name == 'websafeKey':
                setattr(sf, field.name, sess.key.urlsafe())
        sf.check_initialized()
        return sf

    def _createSessionObject(self, request):
        """Create new session object, returning SessionForm request."""
        # User must be authenticated to create Session
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required.')
        # 'name' is a required field
        if not request.name:
            raise endpoints.BadRequestException(
                "Session 'name' field required.")
        # 'websafeConferenceKey' is a required field
        wsck = request.websafeConferenceKey
        if not wsck:
            raise endpoints.BadRequestException(
                'websafeConferenceKey field required.')
        # Get conference and check that it exists
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % (wsck,))

        # check that user is owner
        user_id = getUserId(user)
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the conference owner can add sessions.')

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name)
            for field in request.all_fields()}
        del data['websafeKey']

        # convert date to Date object and check against conference dates
        if data['date']:
            data['date'] = datetime.strptime(
                            data['date'][:10], "%Y-%m-%d").date()
            if not conf.startDate <= data['date'] <= conf.endDate:
                raise endpoints.BadRequestException(
                    'Date does not fall within conference dates.')

        # convert startTime to integer seconds
        if data['startTime']:
            data['startTime'] = getSeconds(data['startTime'])

        # convert ENUM to string
        if data['typeOfSession']:
            data['typeOfSession'] = str(data['typeOfSession'])
        else:
            data['typeOfSession'] = str(TypeOfSession.NOT_SPECIFIED)

        # Generate Session Id and Key based on Conference key
        s_id = Session.allocate_ids(size=1, parent=conf.key)[0]
        s_key = ndb.Key(Session, s_id, parent=conf.key)
        data['key'] = s_key

        # Store session object
        Session(**data).put()

        # If speakers were set on the session, add task to check for
        # featured speaker for the conference and add to memcache.
        if data['speakerKeys']:
            taskqueue.add(
                params={'websafeConferenceKey': wsck},
                url='/tasks/set_featured_speaker'
            )

        return self._copySessionToForm(s_key.get())

    @endpoints.method(SessionForm, SessionForm,
            path='conference/newsession',
            http_method='POST', name='createSession')
    def createSession(self, request):
        """Create new session."""
        return self._createSessionObject(request)

    @endpoints.method(SESS_GET_REQUEST, SessionForms,
            path='conference/{websafeConferenceKey}/sessions',
            name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Return all sessions for requested conference."""
        q = Session.query().filter(
                Session.websafeConferenceKey == request.websafeConferenceKey)
        q = q.order(Session.startTime)
        sessions = q.fetch()

        # return individual SessionForm object per Session
        return SessionForms(
            sessions=[self._copySessionToForm(s) for s in sessions]
        )

    @endpoints.method(SESS_TYPE_GET, SessionForms,
            path='conference/{websafeConferenceKey}/{typeOfSession}',
            name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Return all sessions of a given type for a given conference."""
        q = Session.query().filter(
                Session.websafeConferenceKey == request.websafeConferenceKey,
                Session.typeOfSession == request.typeOfSession)
        q = q.order(Session.startTime)
        sessions = q.fetch()

        # return individual SessionForm object per Session
        return SessionForms(
            sessions=[self._copySessionToForm(s) for s in sessions]
        )

    @endpoints.method(SESS_SPEAKER_GET, SessionForms,
            path='sessions/{websafeSpeakerKey}',
            name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Return all sessions from all conferences featuring a given
        speaker."""
        q = Session.query().filter(
                Session.speakerKeys == request.websafeSpeakerKey)
        q = q.order(Session.websafeConferenceKey)
        sessions = q.fetch()

        # return individual SessionForm object per Session
        return SessionForms(
            sessions=[self._copySessionToForm(s) for s in sessions]
        )

    @endpoints.method(SESS_GET_REQUEST, SessionForms,
            path='conference/{websafeConferenceKey}/sessions/popular',
            name='getSessionsPopular')
    def getSessionsPopular(self, request):
        """Returns top three most popular sessions for a given conference."""
        # get all sessions for the conference
        q = Session.query().filter(
                Session.websafeConferenceKey == request.websafeConferenceKey)
        sessions = q.fetch()

        # Create a list of dicts that marry Session objects, their websafe keys
        # and a count of how frequently they appear in user wishlists.
        s_list = []
        for s in sessions:
            websafeKey = s.key.urlsafe()
            frequency = Profile.query().\
                filter(Profile.sessionWishlistKeys == websafeKey).\
                count()
            if frequency > 0:
                s_list.append({
                    'session': s,
                    'websafeKey': websafeKey,
                    'frequency': frequency
                    })

        # Sort the session list
        s_list.sort(key=lambda session: session['frequency'], reverse=True)

        # Find the top 3 sessions by their frequency rating
        if len(s_list) >= 3:
            top_three = s_list[:3]
        else:
            top_three = s_list  # In this case, will be less than three

        # return individual SessionForm object per Session
        return SessionForms(
            sessions=[self._copySessionToForm(s['session']) for s in top_three]
        )

    @endpoints.method(SESS_HARD_QUERY_POST, SessionForms,
            path='conference/{websafeConferenceKey}/sessions/hard',
            http_method='POST',
            name='getSessionsHardQuery')
    def getSessionsHardQuery(self, request):
        """Return sessions not of certain type and before certain time."""
        # Create filter node for websafeConferenceKey
        wsck = request.websafeConferenceKey
        confFilter = ndb.query.FilterNode('websafeConferenceKey', '=', wsck)

        # Convert the passed in time string to integer seconds and store
        # as a filter node object.
        beforeTime = getSeconds(request.beforeTime)
        timeFilter = ndb.query.FilterNode('startTime', '<', beforeTime)

        # We can't use a '!=', as this will result in too many inequality
        # filters due to the implementation (see README.md).
        # Instead, we add equality filters for every session type except the
        # one we're filtering out.
        type_filters = []
        for session_type in TypeOfSession:
            if str(session_type) != request.notTypeOfSession:
                filter_node = ndb.query.FilterNode(
                                'typeOfSession', '=', str(session_type))
                type_filters.append(filter_node)

        q = Session.query(ndb.OR(
                            ndb.AND(confFilter, timeFilter, type_filters[0]),
                            ndb.AND(confFilter, timeFilter, type_filters[1]),
                            ndb.AND(confFilter, timeFilter, type_filters[2]),
                            ndb.AND(confFilter, timeFilter, type_filters[3]),
                            ndb.AND(confFilter, timeFilter, type_filters[4]),
                            ndb.AND(confFilter, timeFilter, type_filters[5])))
        sessions = q.fetch()

        # return individual SessionForm object per Session
        return SessionForms(
            sessions=[self._copySessionToForm(s) for s in sessions]
        )

# - - - Session Wishlists - - - - - - - - - - - - - - - - - -

    def _addToWishlist(self, request):
        """Add a session to user's session wishlist."""
        prof = self._getProfileFromUser()

        # Add session key to profile object
        wssk = request.websafeSessionKey
        if wssk not in prof.sessionWishlistKeys:
            prof.sessionWishlistKeys.append(wssk)
            prof.put()
            retval = True
        else:
            retval = False

        return BooleanMessage(data=retval)

    @endpoints.method(SESS_WISHLIST_POST, BooleanMessage,
            path='wishlist/add/{websafeSessionKey}',
            http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Add a session to user's session wishlist."""
        return self._addToWishlist(request)

    @endpoints.method(message_types.VoidMessage, SessionForms,
            path='wishlist/all', name='getWishlistAll')
    def getWishlistAll(self, request):
        """Gets all sessions in user's wishlist across all conferences."""
        prof = self._getProfileFromUser()

        # Convert websafe keys to Session keys and get Sessions
        swl_keys = [ndb.Key(urlsafe=s) for s in prof.sessionWishlistKeys]
        sessions = ndb.get_multi(swl_keys)

        # return individual SessionForm object per Session
        return SessionForms(
            sessions=[self._copySessionToForm(s) for s in sessions]
        )

    @endpoints.method(SESS_WISHLIST_GET, SessionForms,
            path='wishlist/{websafeConferenceKey}/sessions',
            name='getSessionsInWishList')
    def getSessionsInWishlist(self, request):
        """Get sessions in user's wishlist for given conference."""
        prof = self._getProfileFromUser()

        # Convert websafe keys to Session keys and get Sessions
        swl_keys = [ndb.Key(urlsafe=s) for s in prof.sessionWishlistKeys]
        sessions = ndb.get_multi(swl_keys)

        # Return only sessions matching requested conference
        conf_sessions = []
        for s in sessions:
            if s.websafeConferenceKey == request.websafeConferenceKey:
                conf_sessions.append(s)

        # return individual SessionForm object per Session
        return SessionForms(
            sessions=[self._copySessionToForm(s) for s in conf_sessions]
        )

# - - - Featured Speaker - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheFeaturedSpeaker(request):
        """Create featured speaker and sessions for a Conference;
        called when new session is created with speaker(s) set.
        """
        wsck = request.get('websafeConferenceKey')
        # Get all sessions for conference. We're specifying an ancestor here
        # to ensure our query uses "strong consistency" and includes the
        # just-added session.
        sessions = ndb.gql("SELECT * "
                           "FROM Session "
                           "WHERE ANCESTOR IS :1 ",
                           ndb.Key(urlsafe=wsck)).fetch()
        speakers_sessions = {}
        # Loop through sessions
        for s in sessions:
            if s.speakerKeys:
                # Loop through speakers for the session
                for s_key in s.speakerKeys:
                    if s_key in speakers_sessions.keys():
                        speakers_sessions[s_key].append(s)
                    else:
                        speakers_sessions[s_key] = [s]
        featured = {'sessions': [],
                    'num_of_sessions': 0,
                    'speaker_key': ""}
        for s_key in speakers_sessions:
            if len(speakers_sessions[s_key]) > featured['num_of_sessions']:
                featured['sessions'] = speakers_sessions[s_key]
                featured['num_of_sessions'] = len(speakers_sessions[s_key])
                featured['speaker_key'] = s_key
        if featured['num_of_sessions'] > 1:
            # If there is a featured speaker (more than one session in this
            # conference), then get speaker data, format message data and
            # set it in memcache.
            speaker = ndb.Key(urlsafe=featured['speaker_key']).get()
            featured_speaker = FEATURED_SPEAKER_TPL % (
                speaker.name,
                ', '.join(s.name for s in featured['sessions'])
            )
            # Memcache key consists of a text string plus a websafe Conference
            # key. This allows us to store featured speakers for multiple
            # conferences simultaneously.
            memcache.set(
                MEMCACHE_FEATURED_SPEAKER_KEY + wsck, featured_speaker)
        else:
            # Even if this speaker wasn't a featured speaker,
            # don't delete memcache entry, as there may be another featured
            # speaker already set for the conference.
            featured_speaker = ""

        return featured_speaker

    @endpoints.method(FEATURED_SPEAKER_GET, StringMessage,
            path='conference/{websafeConferenceKey}/featuredspeaker/get',
            name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Reaturn Featured Speaker and Sessions from memcache."""
        wsck = request.websafeConferenceKey
        memcache_key = MEMCACHE_FEATURED_SPEAKER_KEY + wsck
        return StringMessage(data=memcache.get(memcache_key) or "")

# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement

    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get', http_method='GET',
            name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(
            data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


api = endpoints.api_server([ConferenceApi])  # register API
