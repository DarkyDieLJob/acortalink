FROM nginx:alpine
COPY index.html /usr/share/nginx/html/index.html
COPY favicon.svg /usr/share/nginx/html/favicon.svg
COPY robots.txt /usr/share/nginx/html/robots.txt
COPY sitemap.xml /usr/share/nginx/html/sitemap.xml
COPY llms.txt /usr/share/nginx/html/llms.txt
COPY BingSiteAuth.xml /usr/share/nginx/html/BingSiteAuth.xml
COPY og-image.png /usr/share/nginx/html/og-image.png
COPY og-image.svg /usr/share/nginx/html/og-image.svg
COPY blog/ /usr/share/nginx/html/blog/
COPY js/ /usr/share/nginx/html/js/
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
