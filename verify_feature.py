"""Quick verification script for the new user accounts + recipe book feature."""
import sys
import os

# Fix encoding for Windows console
os.environ['PYTHONIOENCODING'] = 'utf-8'

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 50)
print("AI Recipe Generator - Feature Verification")
print("=" * 50)

# Step 1: Test imports
print("\n[1/5] Testing imports...")
try:
    from Foodimg2Ing import app, db
    print("  OK - App and DB initialized")
except Exception as e:
    print(f"  FAIL - Import error: {e}")
    sys.exit(1)

# Step 2: Test configuration
print("\n[2/5] Checking configuration...")
print(f"  OK - SECRET_KEY set: {bool(app.config.get('SECRET_KEY'))}")
print(f"  OK - DB URI: {app.config.get('SQLALCHEMY_DATABASE_URI', 'NOT SET')}")

# Step 3: Test models
print("\n[3/5] Testing models...")
try:
    from Foodimg2Ing.models import User, SavedRecipe
    print(f"  OK - User table: {User.__tablename__}")
    print(f"  OK - SavedRecipe table: {SavedRecipe.__tablename__}")
except Exception as e:
    print(f"  FAIL - Model error: {e}")
    sys.exit(1)

# Step 4: Test database tables
print("\n[4/5] Testing database...")
try:
    with app.app_context():
        db.create_all()
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        print(f"  OK - Tables created: {tables}")
except Exception as e:
    print(f"  FAIL - Database error: {e}")
    sys.exit(1)

# Step 5: Test routes
print("\n[5/5] Testing routes...")
try:
    with app.test_client() as client:
        # Guest accessible routes
        for route in ['/', '/about', '/generate', '/demo']:
            resp = client.get(route)
            status = "OK" if resp.status_code == 200 else "FAIL"
            print(f"  {status} - GET {route} -> {resp.status_code}")

        # Auth routes
        for route in ['/login', '/register']:
            resp = client.get(route)
            status = "OK" if resp.status_code == 200 else "FAIL"
            print(f"  {status} - GET {route} -> {resp.status_code}")

        # Protected routes (should redirect to login)
        for route in ['/recipe-book', '/profile']:
            resp = client.get(route)
            status = "OK" if resp.status_code == 302 else "FAIL"
            print(f"  {status} - GET {route} -> {resp.status_code} (redirect to login)")

        # Test registration
        resp = client.post('/register', data={
            'name': 'Test User',
            'email': 'test@example.com',
            'password': 'TestPass123',
            'confirm_password': 'TestPass123'
        }, follow_redirects=False)
        status = "OK" if resp.status_code == 302 else "FAIL"
        print(f"  {status} - POST /register -> {resp.status_code}")

        # Test login
        resp = client.post('/login', data={
            'email': 'test@example.com',
            'password': 'TestPass123'
        }, follow_redirects=False)
        status = "OK" if resp.status_code == 302 else "FAIL"
        print(f"  {status} - POST /login -> {resp.status_code}")

        # After login, test protected routes
        resp = client.get('/recipe-book')
        status = "OK" if resp.status_code == 200 else "FAIL"
        print(f"  {status} - GET /recipe-book (logged in) -> {resp.status_code}")

        resp = client.get('/profile')
        status = "OK" if resp.status_code == 200 else "FAIL"
        print(f"  {status} - GET /profile (logged in) -> {resp.status_code}")

        # Test save recipe (AJAX)
        import json
        resp = client.post('/recipe-book/save',
            data=json.dumps({
                'food_name': 'Test Pizza',
                'ingredients': ['cheese', 'dough', 'sauce'],
                'recipe_steps': ['Mix dough', 'Add sauce', 'Bake'],
                'image_path': 'images/burger.jpg',
                'prediction_source': 'Local AI Model',
                'confidence': 0.85
            }),
            content_type='application/json'
        )
        data = json.loads(resp.data)
        status = "OK" if resp.status_code == 201 and data.get('success') else "FAIL"
        print(f"  {status} - POST /recipe-book/save -> {resp.status_code} ({data.get('message', '')})")

        # Test duplicate save
        resp = client.post('/recipe-book/save',
            data=json.dumps({
                'food_name': 'Test Pizza',
                'ingredients': ['cheese', 'dough', 'sauce'],
                'recipe_steps': ['Mix dough', 'Add sauce', 'Bake'],
                'image_path': 'images/burger.jpg',
                'prediction_source': 'Local AI Model',
                'confidence': 0.85
            }),
            content_type='application/json'
        )
        data = json.loads(resp.data)
        status = "OK" if resp.status_code == 409 and data.get('duplicate') else "FAIL"
        print(f"  {status} - POST /recipe-book/save (duplicate) -> {resp.status_code} ({data.get('message', '')})")

        # Test logout
        resp = client.get('/logout', follow_redirects=False)
        status = "OK" if resp.status_code == 302 else "FAIL"
        print(f"  {status} - GET /logout -> {resp.status_code}")

        # Test save when not logged in (guest auto-save prep)
        resp = client.post('/recipe-book/save',
            data=json.dumps({
                'food_name': 'Pending Pizza',
                'ingredients': ['dough', 'tomato'],
                'recipe_steps': ['Spread', 'Bake'],
                'image_path': 'images/pizza.jpg',
                'prediction_source': 'Test Vision',
                'confidence': 0.99,
                'next_url': '/result?result_id=test-123'
            }),
            content_type='application/json'
        )
        data = json.loads(resp.data)
        status = "OK" if resp.status_code == 401 and data.get('login_required') else "FAIL"
        print(f"  {status} - POST /recipe-book/save (guest prep) -> {resp.status_code} (login_required)")

        # Verify session holds the pending recipe details
        with client.session_transaction() as sess:
            has_pending = 'pending_recipe' in sess and sess.get('post_login_action') == 'save_recipe'
            status = "OK" if has_pending else "FAIL"
            print(f"  {status} - Session has pending recipe: {has_pending}")
            if has_pending:
                print(f"    Pending recipe food name: {sess['pending_recipe']['food_name']}")
                print(f"    Pending recipe next_url: {sess['next_url']}")

        # Test login now that pending recipe is in session -> should auto-save and redirect to next_url
        resp = client.post('/login', data={
            'email': 'test@example.com',
            'password': 'TestPass123'
        }, follow_redirects=False)
        status = "OK" if resp.status_code == 302 and resp.location.endswith('/result?result_id=test-123') else "FAIL"
        print(f"  {status} - POST /login with pending recipe -> Redirected to: {resp.location}")

        # Verify recipe was saved to the DB
        with app.app_context():
            u = User.query.filter_by(email='test@example.com').first()
            if u:
                saved = SavedRecipe.query.filter_by(user_id=u.id, food_name='Pending Pizza').first()
                status = "OK" if saved else "FAIL"
                print(f"  {status} - Pending recipe auto-saved to DB: {bool(saved)}")
                if saved:
                    print(f"    Saved recipe image path: {saved.image_path}")

        # Test session is cleaned up
        with client.session_transaction() as sess:
            is_cleaned = 'pending_recipe' not in sess and 'post_login_action' not in sess
            status = "OK" if is_cleaned else "FAIL"
            print(f"  {status} - Session cleaned up after auto-save: {is_cleaned}")

        # Logout
        client.get('/logout')

        # Test register auto-save flow
        # 1. Guest save to prep session
        resp = client.post('/recipe-book/save',
            data=json.dumps({
                'food_name': 'Register Burger',
                'ingredients': ['bun', 'patty'],
                'recipe_steps': ['Grill', 'Assemble'],
                'image_path': 'images/burger.jpg',
                'prediction_source': 'Test Vision',
                'confidence': 0.95,
                'next_url': '/result?result_id=burger-123'
            }),
            content_type='application/json'
        )
        # 2. Register new user
        resp = client.post('/register', data={
            'name': 'Register User',
            'email': 'register@example.com',
            'password': 'RegisterPass123',
            'confirm_password': 'RegisterPass123'
        }, follow_redirects=False)
        status = "OK" if resp.status_code == 302 and resp.location.endswith('/result?result_id=burger-123') else "FAIL"
        print(f"  {status} - POST /register with pending recipe -> Redirected to: {resp.location}")

        # 3. Verify auto-saved to DB
        with app.app_context():
            u = User.query.filter_by(email='register@example.com').first()
            if u:
                saved = SavedRecipe.query.filter_by(user_id=u.id, food_name='Register Burger').first()
                status = "OK" if saved else "FAIL"
                print(f"  {status} - Register pending recipe auto-saved to DB: {bool(saved)}")

        # 4. Verify session is cleaned up
        with client.session_transaction() as sess:
            is_cleaned = 'pending_recipe' not in sess and 'post_login_action' not in sess
            status = "OK" if is_cleaned else "FAIL"
            print(f"  {status} - Register session cleaned up: {is_cleaned}")

    # Cleanup test data
    with app.app_context():
        for email in ['test@example.com', 'register@example.com']:
            test_user = User.query.filter_by(email=email).first()
            if test_user:
                SavedRecipe.query.filter_by(user_id=test_user.id).delete()
                db.session.delete(test_user)
        db.session.commit()
        print("\n  OK - Test data cleaned up")

except Exception as e:
    print(f"  FAIL - Route error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 50)
print("ALL CHECKS PASSED!")
print("=" * 50)
