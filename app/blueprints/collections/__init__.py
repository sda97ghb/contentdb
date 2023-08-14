# ContentDB
# Copyright (C) 2023 rubenwardy
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import re
import typing

from flask import Blueprint, request, redirect, render_template, flash, abort
from flask_babel import lazy_gettext, gettext
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import StringField, BooleanField, SubmitField, FieldList, HiddenField
from wtforms.validators import InputRequired, Length, Optional

from app.models import Collection, db, Package, Permission, CollectionPackage, User, UserRank, AuditSeverity
from app.utils import is_package_page, nonempty_or_none, add_audit_log

bp = Blueprint("collections", __name__)


@bp.route("/collections/")
@bp.route("/collections/<author>/")
def list_all(author=None):
	if author:
		user = User.query.filter_by(username=author).one_or_404()
		query = user.collections
	else:
		user = None
		query = Collection.query.order_by(db.asc(Collection.title))

	if "package" in request.args:
		package = Package.get_by_key(request.args["package"])
		if package is None:
			abort(404)

		query = query.filter(Collection.packages.contains(package))

	collections = [x for x in query.all() if x.check_perm(current_user, Permission.VIEW_COLLECTION)]
	return render_template("collections/list.html",
		user=user, collections=collections,
		noindex=user is None or len(collections) == 0)


@bp.route("/collections/<author>/<name>/")
def view(author, name):
	collection = Collection.query \
		.filter(Collection.name == name, Collection.author.has(username=author)) \
		.one_or_404()

	if not collection.check_perm(current_user, Permission.VIEW_COLLECTION):
		abort(404)

	return render_template("collections/view.html", collection=collection)


class CollectionForm(FlaskForm):
	title = StringField(lazy_gettext("Title"), [InputRequired(), Length(3, 100)])
	short_description = StringField(lazy_gettext("Short Description"), [Optional(), Length(0, 200)])
	private = BooleanField(lazy_gettext("Private"))
	descriptions = FieldList(
		StringField(lazy_gettext("Short Description"), [Optional(), Length(0, 500)], filters=[nonempty_or_none]),
		min_entries=0)
	package_ids = FieldList(HiddenField(), min_entries=0)
	submit = SubmitField(lazy_gettext("Save"))


@bp.route("/collections/new/", methods=["GET", "POST"])
@bp.route("/collections/<author>/<name>/edit/", methods=["GET", "POST"])
@login_required
def create_edit(author=None, name=None):
	collection: typing.Optional[Collection] = None
	if author is not None and name is not None:
		collection = Collection.query \
			.filter(Collection.name == name, Collection.author.has(username=author)) \
			.one_or_404()
		if not collection.check_perm(current_user, Permission.EDIT_COLLECTION):
			abort(403)
	elif "author" in request.args:
		author = request.args["author"]
		if author != current_user.username and not current_user.rank.at_least(UserRank.EDITOR):
			abort(403)

	if author is None:
		author = current_user
	else:
		author = User.query.filter_by(username=author).one()

	form = CollectionForm(formdata=request.form, obj=collection)

	initial_package = None
	if "package" in request.args:
		initial_package = Package.get_by_key(request.args["package"])

	if request.method == "GET":
		# HACK: fix bug in wtforms
		form.private.data = collection.private if collection else False
		if collection:
			for item in collection.items:
				form.descriptions.append_entry(item.description)
				form.package_ids.append_entry(item.package.id)

	if form.validate_on_submit():
		ret = handle_create_edit(collection, form, initial_package, author)
		if ret:
			return ret

	return render_template("collections/create_edit.html",
			collection=collection, form=form)


regex_invalid_chars = re.compile("[^a-z_]")


def handle_create_edit(collection: Collection, form: CollectionForm,
		initial_package: typing.Optional[Package], author: User):

	severity = AuditSeverity.NORMAL if author == current_user else AuditSeverity.EDITOR
	name = collection.name if collection else regex_invalid_chars.sub("", form.title.data.lower().replace(" ", "_"))

	if collection is None:
		if Collection.query \
				.filter(Collection.name == name, Collection.author == author) \
				.count() > 0:
			flash(gettext("A collection with a similar title already exists"), "danger")
			return

		if Package.query \
				.filter(Package.name == name, Package.author == author) \
				.count() > 0:
			flash(gettext("Unable to create collection as a package with that name already exists"), "danger")
			return

		collection = Collection()
		collection.name = name
		collection.author = author
		db.session.add(collection)

		if initial_package:
			link = CollectionPackage()
			link.package = initial_package
			link.collection = collection
			link.order = len(collection.items)
			db.session.add(link)

		add_audit_log(severity, current_user,
				f"Created collection {collection.author.username}/{collection.name}",
				collection.get_url("collections.view"), None)

	else:
		for i, package_id in enumerate(form.package_ids):
			item = next((x for x in collection.items if str(x.package.id) == package_id.data), None)
			if item is None:
				abort(400)

			item.description = form.descriptions[i].data

		add_audit_log(severity, current_user,
				f"Edited collection {collection.author.username}/{collection.name}",
				collection.get_url("collections.view"), None)

	form.populate_obj(collection)
	db.session.commit()
	return redirect(collection.get_url("collections.view"))


def toggle_package(collection: Collection, package: Package):
	severity = AuditSeverity.NORMAL if collection.author == current_user else AuditSeverity.EDITOR

	if package in collection.packages:
		CollectionPackage.query \
			.filter(CollectionPackage.collection == collection, CollectionPackage.package == package) \
			.delete(synchronize_session=False)
		add_audit_log(severity, current_user,
				f"Removed {package.get_id()} from collection {collection.author.username}/{collection.name}",
				collection.get_url("collections.view"), None)
		db.session.commit()
		return False
	else:
		link = CollectionPackage()
		link.package = package
		link.collection = collection
		link.order = len(collection.items)
		db.session.add(link)
		add_audit_log(severity, current_user,
				f"Added {package.get_id()} to collection {collection.author.username}/{collection.name}",
				collection.get_url("collections.view"), None)
		db.session.commit()
		return True


@bp.route("/packages/<author>/<name>/add-to/", methods=["GET", "POST"])
@is_package_page
@login_required
def package_add(package):
	if request.method == "POST":
		collection_id = request.form["collection"]
		collection = Collection.query.get(collection_id)
		if collection is None:
			abort(404)

		if not collection.check_perm(current_user, Permission.EDIT_COLLECTION):
			abort(403)

		if toggle_package(collection, package):
			flash(gettext("Added package to collection"), "success")
		else:
			flash(gettext("Removed package from collection"), "success")

		return redirect(package.get_url("collections.package_add"))

	collections = current_user.collections.all()
	if current_user.rank.at_least(UserRank.EDITOR) and current_user.username != "ContentDB":
		collections.extend(Collection.query.filter(Collection.author.has(username="ContentDB")).all())

	return render_template("collections/package_add_to.html", package=package, collections=collections)


@bp.route("/packages/<author>/<name>/favorite/", methods=["POST"])
@is_package_page
@login_required
def package_toggle_favorite(package):
	collection = Collection.query.filter(Collection.name == "favorites", Collection.author == current_user).first()
	if collection is None:
		collection = Collection()
		collection.title = "Favorites"
		collection.name = "favorites"
		collection.short_description = "My favorites"
		collection.author = current_user
		db.session.add(collection)

	if toggle_package(collection, package):
		flash(gettext("Added package to favorites collection"), "success")
	else:
		flash(gettext("Removed package from favorites collection"), "success")

	return redirect(package.get_url("packages.view"))


@bp.route("/collections/<author>/<name>/clone/", methods=["POST"])
@login_required
def clone(author, name):
	old_collection: typing.Optional[Collection] = Collection.query \
		.filter(Collection.name == name, Collection.author.has(username=author)) \
		.one_or_404()

	index = 0
	new_name = name
	new_title = old_collection.title
	while True:
		if Collection.query \
				.filter(Collection.name == new_name, Collection.author == current_user) \
				.count() == 0:
			break

		index += 1
		new_name = f"{name}_{index}"
		new_title = f"{old_collection.title} ({index})"

	collection = Collection()
	collection.title = new_title
	collection.author = current_user
	collection.short_description = old_collection.short_description
	collection.name = new_name
	collection.private = True
	db.session.add(collection)

	for item in old_collection.items:
		new_item = CollectionPackage()
		new_item.package = item.package
		new_item.collection = collection
		new_item.description = item.description
		new_item.order = item.order
		db.session.add(new_item)

	add_audit_log(AuditSeverity.NORMAL, current_user,
			f"Created collection {collection.name} from {old_collection.author.username}/{old_collection.name} ",
			collection.get_url("collections.view"), None)

	db.session.commit()

	return redirect(collection.get_url("collections.view"))
