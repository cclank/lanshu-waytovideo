import json
import sys

def json_to_netscape(json_file, netscape_file):
    with open(json_file, 'r') as f:
        cookies = json.load(f)
    
    with open(netscape_file, 'w') as f:
        f.write("# Netscape HTTP Cookie File\n")
        f.write("# http://curl.haxx.se/rfc/cookie_spec.html\n")
        f.write("# This is a generated file!  Do not edit.\n\n")
        
        for cookie in cookies:
            domain = cookie.get('domain', '')
            # Netscape format requires domains starting with . to have a flag
            flag = "TRUE" if domain.startswith('.') else "FALSE"
            path = cookie.get('path', '/')
            secure = "TRUE" if cookie.get('secure', False) else "FALSE"
            # Expiration date: handle different keys
            expiry = cookie.get('expirationDate') or cookie.get('expiry') or cookie.get('expires', 0)
            name = cookie.get('name', '')
            value = cookie.get('value', '')
            
            f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{int(expiry)}\t{name}\t{value}\n")

if __name__ == "__main__":
    json_to_netscape(sys.argv[1], sys.argv[2])
