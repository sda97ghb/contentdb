# ContentDB
# Copyright (C) 2018-21 rubenwardy
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


from flask import jsonify, abort, make_response, url_for
from app.logic.releases import LogicError, do_create_vcs_release, do_create_zip_release
from app.models import APIToken, Package, MinetestRelease


def error(code: int, msg: str):
	abort(make_response(jsonify({ "success": False, "error": msg }), code))

# Catches LogicErrors and aborts with JSON error
def run_safe(f, *args, **kwargs):
	try:
		return f(*args, **kwargs)
	except LogicError as e:
		error(e.code, e.message)


def api_create_vcs_release(token: APIToken, package: Package, title: str, ref: str,
		min_v: MinetestRelease = None, max_v: MinetestRelease = None, reason="API"):
	if not token.canOperateOnPackage(package):
		error(403, "API token does not have access to the package")

	rel = run_safe(do_create_vcs_release, token.owner, package, title, ref, None, None, reason)

	return jsonify({
		"success": True,
		"task": url_for("tasks.check", id=rel.task_id),
		"release": rel.getAsDictionary()
	})


def api_create_zip_release(token: APIToken, package: Package, title: str, file, reason="API"):
	if not token.canOperateOnPackage(package):
		error(403, "API token does not have access to the package")

	rel = run_safe(do_create_zip_release, token.owner, package, title, file, None, None, reason)

	return jsonify({
		"success": True,
		"task": url_for("tasks.check", id=rel.task_id),
		"release": rel.getAsDictionary()
	})
