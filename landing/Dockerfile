FROM nginx:alpine
COPY index.html /usr/share/nginx/html/index.html
COPY favicon.svg /usr/share/nginx/html/favicon.svg
COPY robots.txt /usr/share/nginx/html/robots.txt
COPY sitemap.xml /usr/share/nginx/html/sitemap.xml
COPY llms.txt /usr/share/nginx/html/llms.txt
COPY BingSiteAuth.xml /usr/share/nginx/html/BingSiteAuth.xml
COPY blog/ /usr/share/nginx/html/blog/
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
