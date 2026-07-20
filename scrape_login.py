from playwright.sync_api import sync_playwright
import time
from bs4 import BeautifulSoup

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto('https://instaflow-automation.lovable.app/auth')
    time.sleep(5)
    
    page.fill('input[type="email"]', 'alessandrofreitas648@gmail.com')
    page.fill('input[type="password"]', '#Alessandro1010')
    
    page.locator('button', has_text='Entrar').nth(1).click()
    
    time.sleep(8)
    page.goto('https://instaflow-automation.lovable.app/top-posts')
    time.sleep(5)
    
    html = page.content()
    soup = BeautifulSoup(html, 'html.parser')
    
    print('--- NAV LINKS ---')
    for a in soup.find_all('a'):
        print(a.text.strip(), '->', a.get('href'))
        
    print('--- PAGE TEXT ---')
    print(soup.text[:2000])
    browser.close()
