from playwright.sync_api import sync_playwright
import time
import os
from bs4 import BeautifulSoup

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = context.new_page()
        
        print("Going to /auth...")
        page.goto('https://instaflow-automation.lovable.app/auth')
        time.sleep(5)
        
        # Take a screenshot to verify if we need to
        # but we can't see it. We will just dump HTML of auth page first if it fails.
        try:
            print("Trying to fill credentials...")
            page.wait_for_selector('input[type="email"]', timeout=10000)
            page.fill('input[type="email"]', 'alessandrofreitas648@gmail.com')
            page.fill('input[type="password"]', '#Alessandro1010')
            
            # Click submit. Usually it's a button with text like "Sign In" or "Entrar"
            # Let's just press Enter on the password field
            page.press('input[type="password"]', 'Enter')
        except Exception as e:
            print("Failed to login:", e)
            with open('auth_failed.html', 'w', encoding='utf-8') as f:
                f.write(page.content())
            browser.close()
            return
            
        print("Waiting for login to complete...")
        time.sleep(8)
        
        print("Going to /top-posts...")
        page.goto('https://instaflow-automation.lovable.app/top-posts')
        time.sleep(5)
        
        content = page.content()
        with open('lovable.html', 'w', encoding='utf-8') as f:
            f.write(content)
            
        print("HTML dumped to lovable.html successfully!")
        
        soup = BeautifulSoup(content, 'html.parser')
        
        # Print out the layout structure, mostly sidebar links
        print("\n--- SIDEBAR LINKS ---")
        navs = soup.find_all('a')
        for a in navs:
            href = a.get('href')
            if href and href.startswith('/'):
                print(f"{a.text.strip()} -> {href}")
                
        print("\n--- TEXT CONTENT ---")
        for tag in soup.find_all(['h1', 'h2', 'h3']):
            print(tag.name, tag.text.strip())
            
        browser.close()

if __name__ == '__main__':
    run()
