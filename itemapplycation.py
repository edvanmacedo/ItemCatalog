from flask import Flask, render_template, request, redirect, \
     jsonify, url_for, flash, session as login_session, \
     make_response

from sqlalchemy import create_engine, asc, desc
from sqlalchemy.orm import sessionmaker
from database_setup import *

import random
import string
import datetime

from oauth2client.client import flow_from_clientsecrets
from oauth2client.client import FlowExchangeError
import httplib2
import json
import requests
from session_validation import session_auth_needed

app = Flask(__name__)

CLIENT_ID = json.loads(open('client_secrets.json', 'r')
                       .read())['web']['client_id']
APPLICATION_NAME = "Restaurant Menu Application"

# Connect to Database and create database session
engine = create_engine('sqlite:///catalog.db')
Base.metadata.bind = engine

DBSession = sessionmaker(bind=engine)
session = DBSession()

# Flask Routing
# Homepage
@app.route('/')
@app.route('/index/')
def displayCatalog():
    categories = session.query(Category).order_by(asc(Category.name))
    items = session.query(Items).order_by(desc(Items.date)).limit(5)
    return render_template('index.html', categories = categories,
                           items = items)

# Create anti-forgery state token
@app.route('/login')
def showLogin():
    state = ''.join(random.choice(string.ascii_uppercase + string.digits)
                    for x in xrange(32))
    login_session['state'] = state
    #return "The current session state is %s" % login_session['state']
    return render_template('login.html', STATE=state)

@app.route('/gconnect', methods=['POST'])
def gconnect():
    """
    gconnect: Implement connection with google plus
    Returns:
        return output Object with authentication information
    """
    # Validate state token
    if request.args.get('state') != login_session['state']:
        response = make_response(json.dumps('Invalid state parameter.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    # Obtain authorization code
    code = request.data
    try:
        # Upgrade the authorization code into a credentials object
        oauth_flow = flow_from_clientsecrets('client_secrets.json', scope='')
        oauth_flow.redirect_uri = 'postmessage'
        credentials = oauth_flow.step2_exchange(code)
    except FlowExchangeError:
        response = make_response(json.dumps(
            'Failed to upgrade the authorization code.'
        ), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Check that the access token is valid.
    access_token = credentials.access_token
    url = (
        'https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=%s'
        % access_token
    )
    h = httplib2.Http()
    result = json.loads(h.request(url, 'GET')[1])
    # If there was an error in the access token info, abort.
    if result.get('error') is not None:
        response = make_response(json.dumps(result.get('error')), 500)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is used for the intended user.
    gplus_id = credentials.id_token['sub']
    if result['user_id'] != gplus_id:
        response = make_response(json.dumps(
            "Token's user ID doesn't match given user ID."
        ), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the access token is valid for this app.
    if result['issued_to'] != CLIENT_ID:
        response = make_response(json.dumps(
            "Token's client ID does not match app's."
        ), 401)
        print "Token's client ID does not match app's."
        response.headers['Content-Type'] = 'application/json'
        return response

    stored_credentials = login_session.get('credentials')
    stored_gplus_id = login_session.get('gplus_id')
    if stored_credentials is not None and gplus_id == stored_gplus_id:
        response = make_response(json.dumps(
            'Current user is already connected.'
        ),200)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Store the access token in the session for later use.
    login_session['credentials'] = access_token
    login_session['gplus_id'] = gplus_id

    # Get user info
    userinfo_url = "https://www.googleapis.com/oauth2/v1/userinfo"
    params = {'access_token': access_token, 'alt': 'json'}
    answer = requests.get(userinfo_url, params=params)

    data = answer.json()

    login_session['username'] = data['name']
    login_session['picture'] = data['picture']
    login_session['email'] = data['email']

    user_id = getUserID(login_session['email'])
    if not user_id:
        user_id = createUser(login_session)
    login_session['user_id'] = user_id

    output = ''
    output += '<h1>Welcome, '
    output += login_session['username']
    output += '!</h1>'
    output += '<img src="'
    output += login_session['picture']
    output += '"style="width: 300px;height: 300px;border-radius: 150px;"'
    output += '"style="-webkit-border-radius: 150px;"'
    output += '"style="-moz-border-radius: 150px;">'
    flash("you are now logged in as %s" % login_session['username'])
    print "done!"
    return output

@app.route('/gdisconnect')
def gdisconnect():
    """
    gdisconnect: Implement disconnection in google plus and clean up session
    Returns:
        return response with message of success or failure
    """
    credentials = login_session.get('credentials')
    revokeToken = 'https://accounts.google.com/o/oauth2/revoke?token=%s'
    if credentials is None:
        response = make_response(json.dumps(
            'Current user not connected.'
        ), 401)
        response.headers['content-type'] = 'application/json'
        return response

    access_token = credentials
    url = revokeToken % access_token
    h = httplib2.Http()
    result = h.request(url, 'GET')[0]

    if result['status'] == '200':
        del login_session['credentials']
        del login_session['gplus_id']
        del login_session['username']
        del login_session['email']
        del login_session['picture']
        response = redirect(url_for('displayCatalog'))
        flash("You are now logged out.")
        return response
    else:
        response = make_response(json.dumps('Failed to revoke token'), 400)
        response.headers['content-type'] = 'application/json'
        return response

# Category Items from catalogdb
@app.route('/index/<path:category_name>/items/')
def displayCategory(category_name):
    """
    displayCategory: Show items that this category contains and choose
    between two templates, one for user connect with edit and delete options
    and another one for just user view
    Args:
        category_name (string): Category name
    Returns:
        return render_template according with authentication
    """
    currentSession = login_session['user_id']
    categories = session.query(Category).order_by(asc(Category.name))
    category = session.query(Category).filter_by(name=category_name).one()
    items = session.query(Items).filter_by(category=category).order_by(
        asc(Items.name)).all()
    count = session.query(Items).filter_by(category=category).count()
    created_by = getUserInfo(category.user_id)
    if 'username' not in login_session or created_by.id != currentSession:
        return render_template('public_items.html', category = category.name,
                               categories = categories, items = items,
                               count = count
                               )
    else:
        user = getUserInfo(login_session['user_id'])
        return render_template('items.html', category = category.name,
                               categories = categories, items = items,
                               count = count, user=user
                               )

# Display a Specific Item from db
@app.route('/index/<path:category_name>/<path:item_name>/')
def displayItem(category_name, item_name):
    """
    displayItem: Display item and choose two types of templates, one
    for users connected with edit and delete options and another one
    for just visualization
    Args:
        category_name(string): Category name of this item
        item_name(string): Item name
    Returns:
        Returns specif item
    """
    currentSession = login_session['user_id']
    item = session.query(Items).filter_by(name=item_name).one()
    created_by = getUserInfo(item.user_id)
    categories = session.query(Category).order_by(asc(Category.name))
    if 'username' not in login_session or created_by.id != currentSession:
        return render_template('public_item_detail.html',
                               item = item,
                               category = category_name,
                               categories = categories,
                               created_by = created_by
                               )
    else:
        return render_template('item_detail.html',
                               item = item,
                               category = category_name,
                               categories = categories,
                               created_by = created_by
                               )

# Utilities functions
def getUserInfo(user_id):
    user = session.query(User).filter_by(id=user_id).one()
    return user

def getUserID(email):
    try:
        user = session.query(User).filter_by(email=email).one()
        return user.id
    except:
        return None

def createUser(login_session):
    emailSession = login_session['email']
    newUser = User(name = login_session['username'], email = emailSession)
    session.add(newUser)
    session.commit()
    user = session.query(User).filter_by(email = login_session['email']).one()
    return user.id

# Add an item
@app.route('/index/add', methods=['GET', 'POST'])
@session_auth_needed
def add_item():
    """
    add_item: Add an item inside of exist category, authentication necessary
    Returns:
        Redirect to displat catalog page
    """
    categories = session.query(Category).all()
    if request.method == 'POST':
        newItem = Items(
            name=request.form['name'],
            description=request.form['description'],
            category=session.query(Category).filter_by(
                name=request.form['category']).one(),
            date=datetime.datetime.now(),
            user_id=login_session['user_id'])
        session.add(newItem)
        session.commit()
        flash('Item Successfully Added!')
        return redirect(url_for('displayCatalog'))
    else:
        return render_template('add_item.html',
                                categories=categories)

# Add a category and save in catalogdb
@app.route('/index/add_category', methods=['GET', 'POST'])
@session_auth_needed
def add_category():
    """
    add_category: Add a new category, authentication necessary
    Returns:
        Redirect to display category page
    """
    if request.method == 'POST':
        newCategory = Category(
            name=request.form['name'],
            user_id=login_session['user_id'])
        print newCategory
        session.add(newCategory)
        session.commit()
        flash('Success! Category has been added')
        return redirect(url_for('displayCatalog'))
    else:
        flash('Failure! Category could not be added')
        return render_template('add_category.html')

# Delete a category
@app.route('/index/<path:category_name>/delete', methods=['GET', 'POST'])
@session_auth_needed
def delete_category(category_name):
    """
    delete_category: Delete specific category, authentication necessary
    and just the category owner could delete
    Args:
        category_name (string): Category name for delete
    Returns:
        returns Successfully message if it was deleted
    """
    categoryToDelete = session.query(Category).filter_by(
        name=category_name).one()
    created_by = getUserInfo(categoryToDelete.user_id)
    user = getUserInfo(login_session['user_id'])
    if created_by.id != login_session['user_id']:
        flash ("You cannot delete this Category")
        return redirect(url_for('displayCatalog'))
    if request.method =='POST':
        session.delete(categoryToDelete)
        session.commit()
        flash('Successfully Deleted! '+categoryToDelete.name)
        return redirect(url_for('displayCatalog'))
    else:
        return render_template('delete_category.html',
                                category=categoryToDelete)

# Edit esisting category
@app.route('/index/<path:category_name>/edit', methods=['GET', 'POST'])
@session_auth_needed
def edit_category(category_name):
    """
    edit_category: Edit specific category, authentication necessary and
    just the category owner could delete
    Args:
        category_name (string): Category name to edit
    Returns:
        Returns Successfully message if it was edit
    """
    editedCategory = session.query(Category).filter_by(
        name=category_name).one()
    category = session.query(Category).filter_by(name=category_name).one()
    # Check if the logged in is authorized or user is the owner of item
    created_by = getUserInfo(editedCategory.user_id)
    user = getUserInfo(login_session['user_id'])
    # If logged in user is not authorized
    if created_by.id != login_session['user_id']:
        flash ("You cannot edit this Category.")
        return redirect(url_for('displayCatalog'))
    # POST methods to handle form submission
    if request.method == 'POST':
        if request.form['name']:
            editedCategory.name = request.form['name']
        session.add(editedCategory)
        session.commit()
        flash('Success! Category Item Edited!')
        return  redirect(url_for('displayCatalog'))
    else:
        flash('Failure! Category Item no edited, try again')
        return render_template('edit_category.html',
                                categories=editedCategory,
                                category = category)

# Edit an item
@app.route('/index/<path:category_name>/<path:item_name>/edit',
           methods=['GET', 'POST'])
@session_auth_needed
def edit_item(category_name, item_name):
    """
    edit_item: Edit specific item, authentication necessary and
    just item owner could edit
    Args:
        category_name (string): category name that contains item
        item_name (string): item name to edit
    Returns:
        Return Successfully message if it was edit
    """
    editedItem = session.query(Items).filter_by(name=item_name).one()
    categories = session.query(Category).all()
    # See if the logged in user is the owner of item
    created_by = getUserInfo(editedItem.user_id)
    user = getUserInfo(login_session['user_id'])
    # If logged in user != item owner redirect them
    if created_by.id != login_session['user_id']:
        flash ("You are not %s. editing is forbiden" % created_by.name)
        return redirect(url_for('displayCatalog'))
    # POST methods handling form
    if request.method == 'POST':
        if request.form['name']:
            editedItem.name = request.form['name']
        if request.form['description']:
            editedItem.description = request.form['description']
        if request.form['category']:
            category = session.query(Category).filter_by(
                name=request.form['category']).one()
            editedItem.category = category
        time = datetime.datetime.now()
        editedItem.date = time
        session.add(editedItem)
        session.commit()
        flash('Category Item Successfully Edited!')
        return  redirect(url_for('displayCategory',
                                category_name=editedItem.category.name))
    else:
        return render_template('edit_item.html',
                                item=editedItem,
                                categories=categories)

# Delete an item
@app.route('/index/<path:category_name>/<path:item_name>/delete',
           methods=['GET', 'POST'])
@session_auth_needed
def delete_item(category_name, item_name):
    """
    delete_item: Delete specific item, authentication necessary and
    just item owner could delete
    Args:
        category_name (string): Category name that contains item
        item_name: Item name
    Returns:
        Return Successfully message if it was deleted
    """
    itemToDelete = session.query(Items).filter_by(name=item_name).one()
    category = session.query(Category).filter_by(name=category_name).one()
    categories = session.query(Category).all()
    # See if the logged in user is the owner of item
    created_by = getUserInfo(itemToDelete.user_id)
    user = getUserInfo(login_session['user_id'])
    # If logged in user != item owner redirect them
    if created_by.id != login_session['user_id']:
        flash (
            "Delete is forbiden for you. This item belongs to %s"
            % created_by.name
        )
        return redirect(url_for('displayCatalog'))
    if request.method =='POST':
        session.delete(itemToDelete)
        session.commit()
        flash('Item Successfully Deleted! '+itemToDelete.name)
        return redirect(url_for('displayCategory',
                                category_name=category.name))
    else:
        return render_template('delete_item.html',
                                item=itemToDelete)

# API JSON endpoint
@app.route('/index/JSON')
def itemsJSON():
    categories = session.query(Category).all()
    category_dict = [c.serialize for c in categories]
    for c in range(len(category_dict)):
        items = [i.serialize for i in session.query(Items)\
                    .filter_by(category_id=category_dict[c]["id"]).all()]
        if items:
            category_dict[c]["Item"] = items
    return jsonify(Category=category_dict)

@app.route('/index/items/JSON')
def getItemsJSON():
    items = session.query(Items).all()
    return jsonify(items=[i.serialize for i in items])

@app.route('/index/<path:category_name>/items/JSON')
def categoryItemsJSON(category_name):
    category = session.query(Category).filter_by(name=category_name).one()
    items = session.query(Items).filter_by(category=category).all()
    return jsonify(items=[i.serialize for i in items])

@app.route('/index/<path:category_name>/<path:item_name>/JSON')
def ItemJSON(category_name, item_name):
    category = session.query(Category).filter_by(name=category_name).one()
    item = session.query(Items)\
            .filter_by(name=item_name, category=category).one()
    return jsonify(item=[item.serialize])

@app.route('/catalog/categories/JSON')
def categoriesJSON():
    categories = session.query(Category).all()
    return jsonify(categories=[c.serialize for c in categories])

if __name__ == '__main__':
    app.secret_key = "secret_key"
    app.debug = True
    app.run(host = '0.0.0.0', port = 5000)
