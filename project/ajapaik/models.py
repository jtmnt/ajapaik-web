from urllib2 import urlopen
from contextlib import closing
from ujson import loads
from math import degrees
from datetime import datetime
from pandas import DataFrame, Series

from django.db.models import Count, Sum, OneToOneField, DateField
import numpy
from django.contrib.gis.db.models import Model, TextField, FloatField, CharField, BooleanField,\
    ForeignKey, IntegerField, DateTimeField, ImageField, URLField, ManyToManyField, SlugField,\
    PositiveSmallIntegerField, PointField, GeoManager, Manager, NullBooleanField, permalink, PositiveIntegerField
from django.core.validators import MinValueValidator
from django.core.validators import MaxValueValidator
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes import generic
from django.db.models.signals import pre_delete, post_save
from django_extensions.db.fields import json
from django.template.defaultfilters import slugify
from django.core.exceptions import ObjectDoesNotExist
from oauth2client.client import OAuth2Credentials
from oauth2client.django_orm import FlowField, CredentialsField
from sorl.thumbnail import get_thumbnail
from django.contrib.gis.geos import Point
from sklearn.cluster import DBSCAN
from geopy.distance import great_circle
from django.utils.translation import ugettext as _
from bulk_update.manager import BulkUpdateManager

from project.common.models import BaseSource
from project.utils import average_angle
from project.utils import angle_diff


def _calc_trustworthiness(user_id):
    total_tries = 0
    correct_tries = 0
    user_unique_latest_geotags = GeoTag.objects.filter(user=user_id, origin=GeoTag.GAME).distinct("photo_id")\
        .order_by("photo_id", "-created")
    for gt in user_unique_latest_geotags:
        if gt.is_correct:
            correct_tries += 1
        total_tries += 1

    if not correct_tries:
        return 0
    trust = (1 - 0.9 ** correct_tries) * correct_tries / float(total_tries)
    trust = max(trust, 0.01)

    return trust


# TODO: Are these two really needed?
def _make_thumbnail(photo, size):
    image = get_thumbnail(photo.image, size)
    return {"url": image.url, "size": [image.width, image.height]}

def _make_fullscreen(photo):
    image = get_thumbnail(photo.image, "1024x1024", upscale=False)
    return {"url": image.url, "size": [image.width, image.height]}


def _user_post_save(sender, instance, **kwargs):
    print "ajapaik user post save"
    Profile.objects.get_or_create(user=instance)


post_save.connect(_user_post_save, sender=User)


class Area(Model):
    name = CharField(max_length=255)
    lat = FloatField(null=True)
    lon = FloatField(null=True)

    class Meta:
        db_table = "project_area"

    def __unicode__(self):
        return u"%s" % self.name


class AlbumPhoto(Model):
    album = ForeignKey("Album")
    photo = ForeignKey("Photo")
    created = DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "project_albumphoto"

    def __unicode__(self):
        return u"%d - %d" % (self.album.id, self.photo.id)


class Album(Model):
    CURATED, FAVORITES, AUTO= range(3)
    TYPE_CHOICES = (
        (CURATED, "Curated"),
        (FAVORITES, "Favorites"),
        (AUTO, "Auto"),
    )
    name = CharField(max_length=255)
    slug = SlugField(null=True, blank=True, max_length=255)
    description = TextField(null=True, blank=True, max_length=2047)
    subalbum_of = ForeignKey("self", blank=True, null=True, related_name="subalbums")
    atype = PositiveSmallIntegerField(choices=TYPE_CHOICES)
    profile = ForeignKey("Profile", related_name="albums", blank=True, null=True)
    is_public = BooleanField(default=True)
    open = BooleanField(default=False)
    ordered = BooleanField(default=False)
    photos = ManyToManyField("Photo", through="AlbumPhoto", related_name="albums")
    lat = FloatField(null=True, blank=True)
    lon = FloatField(null=True, blank=True)
    geography = PointField(srid=4326, null=True, blank=True, geography=True, spatial_index=True)
    cover_photo = ForeignKey("Photo", null=True, blank=True)
    cover_photo_flipped = BooleanField(default=False)
    photo_count_with_subalbums = IntegerField(default=0)
    rephoto_count_with_subalbums = IntegerField(default=0)
    geotagged_photo_count_with_subalbums = IntegerField(default=0)
    comments_count_with_subalbums = IntegerField(default=0)
    created = DateTimeField(auto_now_add=True)
    modified = DateTimeField(auto_now=True)

    class Meta:
        db_table = "project_album"

    def __unicode__(self):
        return u"%s" % self.name

    def save(self, *args, **kwargs):
        if self.lat and self.lon:
            self.geography = Point(x=float(self.lon), y=float(self.lat), srid=4326)
        if self.id and not self.cover_photo_id and self.photos.count() > 0:
            random_photo = self.photos.order_by('?').first()
            self.cover_photo_id = random_photo.id
            if random_photo.flip:
                self.cover_photo_flipped = random_photo.flip
        if self.id:
            album_photos_qs = self.photos.filter(rephoto_of__isnull=True)
            for sa in self.subalbums.exclude(atype=Album.AUTO):
                album_photos_qs = album_photos_qs | sa.photos.filter(rephoto_of__isnull=True)
            album_photos_with_rephotos_qs = album_photos_qs.filter(rephotos__isnull=False).prefetch_related('rephotos').distinct('id')
            self.photo_count_with_subalbums = album_photos_qs.distinct('id').count()
            self.geotagged_photo_count_with_subalbums = album_photos_qs\
                .filter(lat__isnull=False, lon__isnull=False).distinct('id').count()
            self.rephoto_count_with_subalbums = album_photos_qs.aggregate(rephoto_count=Count('rephotos'))['rephoto_count']
            if not self.rephoto_count_with_subalbums:
                self.rephoto_count_with_subalbums = 0
            self.comments_count_with_subalbums = album_photos_qs.aggregate(comment_count=Sum('fb_comments_count'))['comment_count']
            if not self.comments_count_with_subalbums:
                self.comments_count_with_subalbums = 0
            for each in album_photos_with_rephotos_qs:
                for rp in each.rephotos.all():
                    self.comments_count_with_subalbums += rp.fb_comments_count
            if not self.lat:
                random_photo_with_area = album_photos_qs.filter(area__isnull=False).first()
                if random_photo_with_area:
                    self.lat = random_photo_with_area.area.lat
                    self.lon = random_photo_with_area.area.lon
        super(Album, self).save(*args, **kwargs)

    def light_save(self, *args, **kwargs):
        super(Album, self).save(*args, **kwargs)


def delete_parent(sender, **kwargs):
    try:
        if len(kwargs["instance"].album.photos.all()) == 1:
            kwargs["instance"].album.delete()
    except:
        pass

pre_delete.connect(delete_parent, sender=AlbumPhoto)


class PhotoManager(GeoManager):
    use_for_related_fields = True


class Photo(Model):
    objects = PhotoManager()
    geo = GeoManager()
    bulk = BulkUpdateManager()

    # Removed sorl ImageField because of https://github.com/mariocesar/sorl-thumbnail/issues/295
    image = ImageField(upload_to="uploads", blank=True, null=True, max_length=255,
                       height_field='height', width_field='width')
    image_unscaled = ImageField(upload_to="uploads", blank=True, null=True, max_length=255)
    height = IntegerField(null=True, blank=True)
    width = IntegerField(null=True, blank=True)
    flip = NullBooleanField()
    invert = NullBooleanField()
    stereo = NullBooleanField()
    rotated = IntegerField(null=True, blank=True)
    date = DateTimeField(null=True, blank=True)
    date_text = CharField(max_length=255, blank=True, null=True)
    title = CharField(max_length=255, blank=True, null=True)
    description = TextField(null=True, blank=True)
    author = CharField(null=True, blank=True, max_length=255)
    licence = ForeignKey("Licence", null=True, blank=True)
    types = CharField(max_length=255, blank=True, null=True)
    user = ForeignKey("ajapaik.Profile", related_name="photos", blank=True, null=True)
    level = PositiveSmallIntegerField(default=0)
    guess_level = FloatField(default=3)
    lat = FloatField(null=True, blank=True, validators=[MinValueValidator(-85.05115), MaxValueValidator(85)],
                     db_index=True)
    lon = FloatField(null=True, blank=True, validators=[MinValueValidator(-180), MaxValueValidator(180)],
                     db_index=True)
    geography = PointField(srid=4326, null=True, blank=True, geography=True, spatial_index=True)
    bounding_circle_radius = FloatField(null=True, blank=True)
    address = CharField(max_length=255, blank=True, null=True)
    azimuth = FloatField(null=True, blank=True)
    confidence = FloatField(default=0, null=True, blank=True)
    azimuth_confidence = FloatField(default=0, null=True, blank=True)
    source_key = CharField(max_length=100, null=True, blank=True)
    muis_id = CharField(max_length=100, null=True, blank=True)
    muis_media_id = CharField(max_length=100, null=True, blank=True)
    source_url = URLField(null=True, blank=True, max_length=1023)
    source = ForeignKey("Source", null=True, blank=True)
    device = ForeignKey("Device", null=True, blank=True)
    area = ForeignKey("Area", related_name="areas", null=True, blank=True)
    rephoto_of = ForeignKey("self", blank=True, null=True, related_name="rephotos")
    first_rephoto = DateTimeField(null=True, blank=True)
    latest_rephoto = DateTimeField(null=True, blank=True)
    fb_object_id = CharField(max_length=255, null=True, blank=True)
    fb_comments_count = IntegerField(default=0)
    first_comment = DateTimeField(null=True, blank=True)
    latest_comment = DateTimeField(null=True, blank=True)
    geotag_count = IntegerField(default=0, db_index=True)
    first_geotag = DateTimeField(null=True, blank=True)
    latest_geotag = DateTimeField(null=True, blank=True)
    created = DateTimeField(auto_now_add=True)
    modified = DateTimeField(auto_now=True)
    # scale_factor: old picture's zoom level (float [0.5, 4.0])
    # yaw, pitch, roll: phone orientation (float radians)
    gps_accuracy = FloatField(null=True, blank=True)
    gps_fix_age = FloatField(null=True, blank=True)
    cam_scale_factor = FloatField(null=True, blank=True, validators=[MinValueValidator(0.5), MaxValueValidator(4.0)])
    cam_yaw = FloatField(null=True, blank=True)
    cam_pitch = FloatField(null=True, blank=True)
    cam_roll = FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        db_table = "project_photo"

    @staticmethod
    def get_geotagged_photos_list(qs, bounding_box=None):
        # TODO: Once we have regions, re-implement caching
        data = []
        qs = qs.filter(lat__isnull=False, lon__isnull=False, rephoto_of__isnull=True)\
            .annotate(rephoto_count=Count('rephotos')).values_list('id', 'lat', 'lon', 'azimuth', 'rephoto_count')
        if bounding_box:
            qs = qs.filter(lat__gte=bounding_box[0], lon__gte=bounding_box[1], lat__lte=bounding_box[2],
                           lon__lte=bounding_box[3])
        for p in qs:
            data.append([p[0], p[1], p[2], p[3], p[4]])
        return data

    @staticmethod
    def get_game_json_format_photo(photo):
        # TODO: proper JSON serialization
        ret = {
            "id": photo.id,
            "description": photo.description,
            "date_text": photo.date_text,
            "source_key": photo.source_key,
            "source_url": photo.source_url,
            "source_name": photo.source.description,
            "lat": photo.lat,
            "lon": photo.lon,
            "azimuth": photo.azimuth,
            "flip": photo.flip,
            "big": _make_thumbnail(photo, "700x400"),
            "large": _make_fullscreen(photo),
            "confidence": photo.confidence,
            "total_geotags": photo.geotags.distinct('user').count(),
            "geotags_with_azimuth": photo.geotags.filter(azimuth__isnull=False).count(),
        }
        if photo.lat and photo.lon:
            ret["has_coordinates"] = True
        if photo.azimuth:
            ret["has_azimuth"] = True
        return ret

    @staticmethod
    def get_next_photo_to_geotag(qs, request):
        profile = request.get_user().profile
        trustworthiness = _calc_trustworthiness(profile.pk)

        all_photos_set = qs
        photo_ids = frozenset(all_photos_set.values_list("id", flat=True))

        user_geotags_for_set = GeoTag.objects.filter(user=profile, photo_id__in=photo_ids)
        user_skips_for_set = Skip.objects.filter(user=profile, photo_id__in=photo_ids)

        user_geotagged_photo_ids = list(user_geotags_for_set.distinct("photo_id").values_list("photo_id", flat=True))
        user_skipped_photo_ids = list(user_skips_for_set.distinct("photo_id").values_list("photo_id", flat=True))
        user_has_seen_photo_ids = set(user_geotagged_photo_ids + user_skipped_photo_ids)
        user_skipped_less_geotagged_photo_ids = set(user_skipped_photo_ids) - set(user_geotagged_photo_ids)

        user_seen_all = False
        nothing_more_to_show = False

        if "user_skip_array" not in request.session:
             request.session["user_skip_array"] = []

        if trustworthiness < 0.25:
            # Novice users should only receive the easiest images to prove themselves
            ret_qs = all_photos_set.exclude(id__in=user_has_seen_photo_ids).order_by("guess_level", "-confidence")
            if ret_qs.count() == 0:
                # If the user has seen all the photos, offer at random
                user_seen_all = True
                ret_qs = all_photos_set.order_by("?")
        else:
            # Let's try to show the more experienced users photos they have not yet seen at all
            ret_qs = all_photos_set.exclude(id__in=user_has_seen_photo_ids).order_by('?')
            if ret_qs.count() == 0:
                # If the user has seen them all, let's try showing her photos she
                # has skipped (but not in this session) or not marked an azimuth on
                user_seen_all = True
                ret_qs = all_photos_set.filter(id__in=user_skipped_less_geotagged_photo_ids)\
                    .exclude(id__in=request.session["user_skip_array"]).order_by('?')
                if ret_qs.count() == 0:
                    # This user has skipped them in this session, show her photos that
                    # don't have a correct geotag from her
                    user_incorrect_geotags = user_geotags_for_set.filter(is_correct=False)
                    user_correct_geotags = user_geotags_for_set.filter(is_correct=True)
                    user_incorrectly_geotagged_photo_ids = set(user_incorrect_geotags.distinct("photo_id").values_list("photo_id", flat=True))
                    user_correctly_geotagged_photo_ids = set(user_correct_geotags.distinct("photo_id").values_list("photo_id", flat=True))
                    user_no_correct_geotags_photo_ids = list(user_incorrectly_geotagged_photo_ids - user_correctly_geotagged_photo_ids)
                    ret_qs = all_photos_set.filter(id__in=user_no_correct_geotags_photo_ids).order_by('?')
                    if ret_qs.count() == 0:
                        ret_qs = all_photos_set.order_by('?')
                        nothing_more_to_show = True
        ret = ret_qs.first()
        return [Photo.get_game_json_format_photo(ret), user_seen_all, nothing_more_to_show]


    def __unicode__(self):
        return u"%s - %s (%s) (%s)" % (self.id, self.description, self.date_text, self.source_key)

    @permalink
    def get_detail_url(self):
        return "project.ajapaik.views.photo", [self.id, ]

    @permalink
    def get_absolute_url(self):
        pseudo_slug = self.get_pseudo_slug()
        rephoto = self.rephoto_of
        if rephoto:
            pass
        if pseudo_slug != "":
            return "project.ajapaik.views.photoslug", [self.id, pseudo_slug, ]
        else:
            return "project.ajapaik.views.photo", [self.id, ]


    def get_full_screen_image_size(self):
        w = float(self.width)
        h = float(self.height)
        desired_longest_side = float(1920)
        if w > h:
            desired_width = desired_longest_side
            factor = w / desired_longest_side
            desired_height = h / factor
        else:
            desired_height = desired_longest_side
            factor = h / desired_longest_side
            desired_width = w / factor

        return int(desired_width), int(desired_height)

    def get_pseudo_slug(self):
        if self.description is not None and self.description != "":
            slug = "-".join(slugify(self.description).split("-")[:6])[:60]
        elif self.source_key is not None and self.source_key != "":
            slug = slugify(self.source_key)
        else:
            slug = slugify(self.created.__format__("%Y-%m-%d"))
        return slug

    def get_heatmap_points(self):
        valid_geotags = self.geotags.distinct("user_id").order_by("user_id", "-created")
        data = []
        for each in valid_geotags:
            serialized = [each.lat, each.lon]
            if each.azimuth:
                serialized.append(each.azimuth)
            data.append(serialized)
        return data

    def save(self, *args, **kwargs):
        # Update POSTGIS data on save
        if self.lat and self.lon:
            self.geography = Point(x=float(self.lon), y=float(self.lat), srid=4326)
        # Calculate average coordinates for album
        album_ids = set(AlbumPhoto.objects.filter(photo_id=self.id).values_list("album_id", flat=True))
        albums = Album.objects.filter(id__in=album_ids)
        for a in albums:
            photos_with_location = a.photos.filter(lat__isnull=False, lon__isnull=False).all()
            lat = 0
            lon = 0
            count = 0
            for p in photos_with_location:
                lat += p.lat
                lon += p.lon
                count += 1
            if lat > 0 and lon > 0 and count > 0:
                a.lat = lat / count
                a.lon = lon / count
                a.save()
        if not self.first_rephoto:
            first_rephoto = self.rephotos.order_by('created').first()
            if first_rephoto:
                self.first_rephoto = first_rephoto.created
        last_rephoto = self.rephotos.order_by('-created').first()
        if last_rephoto:
            self.latest_rephoto = last_rephoto.created
        super(Photo, self).save(*args, **kwargs)

    def light_save(self, *args, **kwargs):
        super(Photo, self).save(*args, **kwargs)

    @staticmethod
    def get_centroid(points):
        # FIXME: Really need numpy for this?
        n = points.shape[0]
        sum_lon = numpy.sum(points[:, 1])
        sum_lat = numpy.sum(points[:, 0])
        return sum_lon / n, sum_lat / n

    @staticmethod
    def get_nearest_point(set_of_points, point_of_reference):
        closest_point = None
        closest_dist = None
        for point in set_of_points:
            point = (point[1], point[0])
            dist = great_circle(point_of_reference, point).meters
            if (closest_dist is None) or (dist < closest_dist):
                closest_point = point
                closest_dist = dist
        return closest_point

    # TODO: Cut down on the science library use
    def set_calculated_fields(self):
        photo_difficulty_feedback = DifficultyFeedback.objects.filter(photo_id=self.id)
        weighed_level_sum, total_weight = 0, 0
        for each in photo_difficulty_feedback:
            weighed_level_sum += float(each.level) * each.trustworthiness
            total_weight += each.trustworthiness
        if total_weight != 0:
            self.guess_level = round((weighed_level_sum / total_weight), 2)

        if not self.bounding_circle_radius:
            geotags = GeoTag.objects.filter(photo_id=self.id)
            unique_user_geotag_ids = geotags.distinct("user_id").order_by("user_id", "-created")\
                .values_list("id", flat=True)
            self.geotag_count = len(unique_user_geotag_ids)
            unique_user_geotags = geotags.filter(pk__in=unique_user_geotag_ids)
            geotag_coord_map = {}
            for g in unique_user_geotags:
                key = str(g.lat) + str(g.lon)
                if key in geotag_coord_map:
                    geotag_coord_map[key].append(g)
                else:
                    geotag_coord_map[key] = [g]
            if unique_user_geotags:
                df = DataFrame(data=[[x.lon, x.lat] for x in unique_user_geotags], columns=["lon", "lat"])
                coordinates = df.as_matrix(columns=["lon", "lat"])
                db = DBSCAN(eps=0.0003, min_samples=1).fit(coordinates)
                labels = db.labels_
                num_clusters = len(set(labels)) - (1 if -1 in labels else 0)
                clusters = Series([coordinates[labels == i] for i in range(num_clusters)])
                lon = []
                lat = []
                members = []
                for i, cluster in clusters.iteritems():
                    if len(cluster) < 3:
                        representative_point = (cluster[0][1], cluster[0][0])
                    else:
                        representative_point = self.get_nearest_point(cluster, self.get_centroid(cluster))
                    lat.append(representative_point[0])
                    lon.append(representative_point[1])
                    members.append(cluster)
                rs = DataFrame({"lat": lat, "lon": lon, "members": members})
                max_trust = 0
                point = None
                selected_geotags = None
                for a in rs.itertuples():
                    trust_sum = 0
                    current_geotags = []
                    for each in a[3]:
                        g = geotag_coord_map[str(each[1]) + str(each[0])]
                        for gg in g:
                            current_geotags.append(gg)
                            trust_sum += gg.trustworthiness
                    if trust_sum >= max_trust:
                        max_trust = trust_sum
                        point = {"lat": a[1], "lon": a[2]}
                        selected_geotags = current_geotags
                if point:
                    self.lat = point["lat"]
                    self.lon = point["lon"]
                    self.confidence = float(len(selected_geotags)) / float(len(geotags))
                geotags.update(is_correct=False, azimuth_correct=False)
                if selected_geotags:
                    GeoTag.objects.filter(pk__in=[x.id for x in selected_geotags]).update(is_correct=True)
                    # TODO: Solution for few very different guesses e.g. (0, 90, 180) => 90
                    filter_indices = []
                    contains_outliers = True
                    arr = [x.azimuth for x in selected_geotags if x.azimuth]
                    initial_arr_length = len(arr)
                    deg_avg = None
                    if initial_arr_length > 0:
                        while contains_outliers:
                            avg = average_angle(arr)
                            deg_avg = degrees(avg)
                            diff_arr = [angle_diff(x, deg_avg) for x in arr]
                            contains_outliers = False
                            for i, e in enumerate(diff_arr):
                                if e > 60:
                                    filter_indices.append(i)
                                    contains_outliers = True
                            arr = [i for j, i in enumerate(arr) if j not in filter_indices]
                    correct_azimuth_geotags = [i for j, i in enumerate(selected_geotags) if j not in filter_indices]
                    GeoTag.objects.filter(pk__in=[x.id for x in correct_azimuth_geotags]).update(azimuth_correct=True)
                    if deg_avg is not None:
                        self.azimuth = deg_avg
                        self.azimuth_confidence = float(len(arr)) / float(initial_arr_length)
                    else:
                        self.azimuth = None
                        self.azimuth_confidence = None


class PhotoMetadataUpdate(Model):
    photo = ForeignKey("Photo", related_name='metadata_updates')
    old_title = CharField(max_length=255, blank=True, null=True)
    new_title = CharField(max_length=255, blank=True, null=True)
    old_description = TextField(null=True, blank=True)
    new_description = TextField(null=True, blank=True)
    old_author = CharField(null=True, blank=True, max_length=255)
    new_author = CharField(null=True, blank=True, max_length=255)
    created = DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "project_photometadataupdate"


class PhotoComment(Model):
    photo = ForeignKey("Photo", related_name='comments')
    fb_comment_id = CharField(max_length=255, unique=True)
    fb_object_id = CharField(max_length=255)
    fb_comment_parent_id = CharField(max_length=255, blank=True, null=True)
    fb_user_id = CharField(max_length=255)
    text = TextField()
    created = DateTimeField()

    class Meta:
        db_table = "project_photocomment"

    def __unicode__(self):
        return u"%s" % self.text[:50]


class DifficultyFeedback(Model):
    photo = ForeignKey("Photo")
    user_profile = ForeignKey("Profile")
    level = PositiveSmallIntegerField()
    trustworthiness = FloatField()
    geotag = ForeignKey("GeoTag")
    created = DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "project_difficultyfeedback"


class FlipFeedback(Model):
    photo = ForeignKey("Photo")
    user_profile = ForeignKey("Profile")
    flip = NullBooleanField()
    created = DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "project_flipfeedback"


class Points(Model):
    GEOTAG, REPHOTO, PHOTO_UPLOAD, PHOTO_CURATION = range(4)
    ACTION_CHOICES = (
        (GEOTAG, "Geotag"),
        (REPHOTO, "Rephoto"),
        (PHOTO_UPLOAD, "Photo upload"),
        (PHOTO_CURATION, "Photo curation"),
    )

    user = ForeignKey("Profile", related_name="points")
    action = PositiveSmallIntegerField(choices=ACTION_CHOICES)
    photo = ForeignKey("Photo", null=True, blank=True)
    geotag = ForeignKey("GeoTag", null=True, blank=True)
    points = IntegerField(default=0)
    created = DateTimeField(db_index=True)

    class Meta:
        db_table = "project_points"
        verbose_name_plural = "Points"
        unique_together = (("user", "geotag"),)

    def __unicode__(self):
        return u"%d - %s - %d" % (self.user.id, self.action, self.points)


class GeoTag(Model):
    MAP, EXIF, GPS, CONFIRMATION = range(4)
    TYPE_CHOICES = (
        (MAP, _("Map")),
        (EXIF, _("EXIF")),
        (GPS, _("GPS")),
        (CONFIRMATION, _("Confirmation")),
    )
    GAME, MAP_VIEW, GRID = range(3)
    ORIGIN_CHOICES = (
        (GAME, _("Game")),
        (MAP_VIEW, _("Map view")),
        (GRID, _("Grid")),
    )
    GOOGLE_MAP, GOOGLE_SATELLITE, OPEN_STREETMAP = range(3)
    MAP_TYPE_CHOICES = (
        (GOOGLE_MAP, _("Google map")),
        (GOOGLE_SATELLITE, _("Google satellite")),
        (OPEN_STREETMAP, _("OpenStreetMap"))
    )
    lat = FloatField(validators=[MinValueValidator(-85.05115), MaxValueValidator(85)])
    lon = FloatField(validators=[MinValueValidator(-180), MaxValueValidator(180)])
    geography = PointField(srid=4326, null=True, blank=True, geography=True, spatial_index=True)
    azimuth = FloatField(null=True, blank=True)
    azimuth_line_end_lat = FloatField(null=True, blank=True)
    azimuth_line_end_lon = FloatField(null=True, blank=True)
    zoom_level = IntegerField(null=True, blank=True)
    origin = PositiveSmallIntegerField(choices=ORIGIN_CHOICES, default=0)
    type = PositiveSmallIntegerField(choices=TYPE_CHOICES, default=0)
    map_type = PositiveSmallIntegerField(choices=MAP_TYPE_CHOICES, default=0)
    hint_used = BooleanField(default=False)
    user = ForeignKey("Profile", related_name="geotags")
    photo = ForeignKey("Photo", related_name="geotags")
    is_correct = BooleanField(default=False)
    azimuth_correct = BooleanField(default=False)
    score = IntegerField(null=True, blank=True)
    azimuth_score = IntegerField(null=True, blank=True)
    trustworthiness = FloatField()
    created = DateTimeField(auto_now_add=True)
    modified = DateTimeField(auto_now=True)

    class Meta:
        db_table = "project_geotag"

    def save(self, *args, **kwargs):
        self.geography = Point(x=float(self.lon), y=float(self.lat), srid=4326)
        super(GeoTag, self).save(*args, **kwargs)

    def __unicode__(self):
        # Django admin may crash with too long names
        title = None
        desc = None
        if self.photo.title:
            title = self.photo.title[:50]
        if self.photo.description:
            desc = self.photo.description[:50]
        return u"%s - %s - %d" % (title, desc, self.user.id)


class FacebookManager(Manager):
    @staticmethod
    def url_read(uri):
        with closing(urlopen(uri)) as request:
            return request.read()

    def get_user(self, access_token):
        data = loads(self.url_read("https://graph.facebook.com/v2.3/me?access_token=%s" % access_token))
        if not data:
            raise Exception("Facebook did not return anything useful for this access token")

        try:
            return self.get(fb_id=data.get("id")), data
        except ObjectDoesNotExist:
            return None, data,


class Profile(Model):
    objects = BulkUpdateManager()
    facebook = FacebookManager()

    user = OneToOneField(User, primary_key=True)

    fb_name = CharField(max_length=255, null=True, blank=True)
    fb_link = CharField(max_length=255, null=True, blank=True)
    fb_id = CharField(max_length=100, null=True, blank=True)
    fb_token = CharField(max_length=511, null=True, blank=True)
    fb_hometown = CharField(max_length=511, null=True, blank=True)
    fb_current_location = CharField(max_length=511, null=True, blank=True)
    fb_birthday = DateField(null=True, blank=True)
    fb_email = CharField(max_length=255, null=True, blank=True)
    fb_user_friends = TextField(null=True, blank=True)

    google_plus_id = CharField(max_length=100, null=True, blank=True)
    google_plus_email = CharField(max_length=255, null=True, blank=True)
    google_plus_link = CharField(max_length=255, null=True, blank=True)
    google_plus_name = CharField(max_length=255, null=True, blank=True)
    google_plus_token = TextField(null=True, blank=True)
    google_plus_picture = CharField(max_length=255, null=True, blank=True)

    modified = DateTimeField(auto_now=True)

    score = PositiveIntegerField(default=0)
    score_rephoto = PositiveIntegerField(default=0)
    score_recent_activity = PositiveIntegerField(default=0)

    class Meta:
        db_table = 'project_profile'

    @property
    def id(self):
        return self.user_id

    def __unicode__(self):
        return u"%d - %s - %s" % (self.user.id, self.user.username, self.user.get_full_name())

    def update_from_fb_data(self, token, data):
        self.user.first_name = data.get("first_name")
        self.user.last_name = data.get("last_name")
        self.user.save()

        self.fb_token = token
        self.fb_id = data.get("id")
        self.fb_name = data.get("name")
        self.fb_link = data.get("link")
        self.fb_email = data.get("email")
        try:
            self.fb_birthday = datetime.strptime(data.get("birthday"), "%m/%d/%Y")
        except TypeError:
            pass
        location = data.get("location")
        if location is not None and "name" in location:
            self.fb_current_location = location["name"]
        hometown = data.get("hometown")
        if hometown is not None and "name" in hometown:
            self.fb_hometown = data.get("hometown")["name"]
        user_friends = data.get("user_friends")
        if user_friends is not None:
            self.fb_user_friends = user_friends

        self.save()

    def update_from_google_plus_data(self, token, data):
        # TODO: Make form
        if 'given_name' in data:
            self.user.first_name = data["given_name"]
        if 'family_name' in data:
            self.user.last_name = data["family_name"]
        self.user.save()

        if isinstance(token, OAuth2Credentials):
            self.google_plus_token = loads(token.to_json())['access_token']
        else:
            self.google_plus_token = token
        self.google_plus_id = data["id"]
        if 'link' in data:
            self.google_plus_link = data["link"]
        if 'name' in data:
            self.google_plus_name = data["name"]
        if 'email' in data:
            self.google_plus_email = data["email"]
        if 'picture' in data:
            self.google_plus_picture = data["picture"]
        self.save()

    def merge_from_other(self, other):
        other.photos.update(user=self)
        other.skips.update(user=self)
        other.geotags.update(user=self)
        other.points.update(user=self)

    def update_rephoto_score(self):
        photo_ids_rephotographed_by_this_user = Photo.objects.filter(
            rephoto_of__isnull=False, user=self.user).values_list("rephoto_of", flat=True)
        original_photos = Photo.objects.filter(id__in=set(photo_ids_rephotographed_by_this_user))

        user_rephoto_score = 0

        for p in original_photos:
            oldest_rephoto = None
            rephotos_by_this_user = []
            for rp in p.rephotos.all():
                if rp.user and rp.user.id == self.user.id:
                    rephotos_by_this_user.append(rp)
                if not oldest_rephoto or rp.created < oldest_rephoto.created:
                    oldest_rephoto = rp
            oldest_rephoto_is_from_this_user = oldest_rephoto.user and self.user and oldest_rephoto.user.id == self.user.id
            user_first_bonus_earned = False
            if oldest_rephoto_is_from_this_user:
                user_first_bonus_earned = True
                user_rephoto_score += 1250
                try:
                    Points.objects.get(action=Points.REPHOTO, photo=oldest_rephoto)
                except ObjectDoesNotExist:
                    new_record = Points(
                        user=oldest_rephoto.user,
                        action=Points.REPHOTO,
                        photo=oldest_rephoto,
                        points=1250,
                        created=oldest_rephoto.created
                    )
                    new_record.save()
            for rp in rephotos_by_this_user:
                current_score = 250
                if rp.id == oldest_rephoto.id:
                    continue
                else:
                    if not user_first_bonus_earned:
                        current_score = 1000
                        user_first_bonus_earned = True
                    # Check that we have a record in the scoring table
                    try:
                        Points.objects.get(action=Points.REPHOTO, photo=rp)
                    except ObjectDoesNotExist:
                        new_record = Points(
                            user=rp.user,
                            action=Points.REPHOTO,
                            photo=rp,
                            points=current_score,
                            created=rp.created
                        )
                        new_record.save()
                    user_rephoto_score += current_score

        self.score_rephoto = user_rephoto_score
        self.save()

    def set_calculated_fields(self):
        all_time_score = 0
        for g in self.geotags.all():
            if g.score:
                all_time_score += g.score
        self.score = all_time_score
        self.score += self.score_rephoto

# For Google login
class FlowModel(Model):
    id = ForeignKey(User, primary_key=True)
    flow = FlowField()

    class Meta:
        db_table = 'project_flowmodel'


# For Google login
class CredentialsModel(Model):
    id = ForeignKey(User, primary_key=True)
    credential = CredentialsField()

    class Meta:
        db_table = 'project_credentialsmodel'


class Source(BaseSource):
    pass


class Device(Model):
    camera_make = CharField(null=True, blank=True, max_length=255)
    camera_model = CharField(null=True, blank=True, max_length=255)
    lens_make = CharField(null=True, blank=True, max_length=255)
    lens_model = CharField(null=True, blank=True, max_length=255)
    software = CharField(null=True, blank=True, max_length=255)

    class Meta:
        db_table = 'project_device'

    def __unicode__(self):
        return '%s %s %s %s %s' % (self.camera_make, self.camera_model, self.lens_make, self.lens_model, self.software)


class Skip(Model):
    user = ForeignKey('Profile', related_name='skips')
    photo = ForeignKey('Photo')
    created = DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'project_skip'

    def __unicode__(self):
        return '%i %i' % (self.user.pk, self.photo.pk)


# TODO: Do we need this? Kind of violating users' privacy, no?
class Action(Model):
    type = CharField(max_length=255)
    related_type = ForeignKey(ContentType, null=True, blank=True)
    related_id = PositiveIntegerField(null=True, blank=True)
    related_object = generic.GenericForeignKey('related_type', 'related_id')
    params = json.JSONField(null=True, blank=True)

    @classmethod
    def log(cls, my_type, params=None, related_object=None, request=None):
        obj = cls(type=my_type, params=params)
        if related_object:
            obj.related_object = related_object
        obj.save()
        return obj

    class Meta:
        db_table = 'project_action'


class CSVPhoto(Photo):
    # This is a fake class for adding an admin page
    class Meta:
        proxy = True

        # Possible fix for proxy models not getting their auto-generated permissions and stuff
        # class Migration(SchemaMigration):
        # 	def forwards(self, orm):
        # 		orm.send_create_signal('project', ['CSVPhoto'])
        #
        # 	def backwards(self, orm):
        # 		pass


class Licence(Model):
    name = CharField(max_length=255)
    url = URLField(blank=True, null=True)
    image_url = URLField(blank=True, null=True)

    class Meta:
        db_table = 'project_licence'

    def __unicode__(self):
        return '%s' % self.name


class GoogleMapsReverseGeocode(Model):
    lat = FloatField(validators=[MinValueValidator(-85.05115), MaxValueValidator(85)], db_index=True)
    lon = FloatField(validators=[MinValueValidator(-180), MaxValueValidator(180)], db_index=True)
    response = json.JSONField()

    class Meta:
        db_table = 'project_googlemapsreversegeocode'

    def __unicode__(self):
        return '%d;%d' % (self.lat, self.lon)