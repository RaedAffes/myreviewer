import { Injectable, inject } from '@angular/core';
import { Meta, Title } from '@angular/platform-browser';

export interface SeoOptions {
  title: string;
  description?: string;
  canonical?: string;
  noindex?: boolean;
}

const SITE_URL = 'https://myreviewer.tech';

@Injectable({ providedIn: 'root' })
export class SeoService {
  private readonly meta = inject(Meta);
  private readonly title = inject(Title);

  apply(options: SeoOptions): void {
    const { title, description, canonical, noindex } = options;
    this.title.setTitle(title);

    if (description) {
      this.meta.updateTag({ name: 'description', content: description });
      this.meta.updateTag({ property: 'og:description', content: description });
    }

    if (canonical) {
      this.meta.addTag({ rel: 'canonical', href: `${SITE_URL}${canonical}` }, true);
      this.meta.updateTag({ property: 'og:url', content: `${SITE_URL}${canonical}` });
    }

    if (noindex) {
      this.meta.updateTag({ name: 'robots', content: 'noindex, nofollow' });
    } else {
      this.meta.updateTag({ name: 'robots', content: 'index, follow' });
    }

    this.meta.updateTag({ property: 'og:title', content: title });
    this.meta.updateTag({ name: 'twitter:title', content: title });
  }
}