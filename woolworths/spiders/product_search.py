# -*- coding: utf-8 -*-
import scrapy
from selenium import webdriver
from scrapy.spiders import CrawlSpider, Rule
from scrapy.http import TextResponse, Response
from scrapy.linkextractors import LinkExtractor
from lxml import html
import logging
import re


class Category(scrapy.Item):
    '''
    Category title and URL, only used to internal message transport between
    request/response
    '''
    title = scrapy.Field()
    url = scrapy.Field()


class Item(scrapy.Item):
    '''
    Product Item
    '''
    name = scrapy.Field()
    price_amount = scrapy.Field()
    price_cup = scrapy.Field()
    size = scrapy.Field()
    url = scrapy.Field()
    category = scrapy.Field()
    sub_category = scrapy.Field()
    parent_url = scrapy.Field()


def debug_response(self, response):
    '''
    View response
    '''
    from scrapy.shell import inspect_response
    inspect_response(response, self)


class ProductSearchSpider(scrapy.Spider):
    '''
    Apparent web scrape strategry/pathway:

    parent [nav parent]--
            |
            ---> sub category [nav child]
                                        |
                                        --> product 1
                                        --> product 2
                                        ...
                                        ---> product n
           ---> sub category [nav child]
                                        |
                                        --> product 1
                                        --> product 2
                                        ...
                                        ---> product n

    The product lists for the bottom category is a scroll-to-load SPA.

    The scrape stages are as follows:
        1. collect top-level categories on first parse
        1.1 yield the request for the URL of each category collected
        2. collect the child categories for each requestd parent category
        2.1 yield the request fro the URL of the page containing the products
        3. scrape items in a scroll-to-load fashion.
        3.1 yield items into mongoDB pipeline
        4. repeat 3. on-next link from sub-category page
    '''
    name = "products"
    allowed_domains = ["woolworths.com.au"]
    start_urls = ['https://www.woolworths.com.au/Shop/Browse', ]

    def __init__(self, *args, **kwargs):
        self.driver = webdriver.Chrome()
        self.domain = "https://www.woolworths.com.au"
        self.count = 0

    def parse(self, response):
        '''
        stage 1.
        Initial parser, collects the top level categories from page.
        Yielding the request for each parent URL found.
        '''
        self.driver.get(response.url)

        response = TextResponse(
            url=response.url, body=self.driver.page_source, encoding='utf-8')

        for category in response.xpath('//div[@ng-class="::aisleClass"]'):
            category_item = Category()
            category_item['title'] = category.xpath(
                './/span[@class="categoryList-aisleLabelNameLine"]/text()'
            ).extract()[0]
            category_item['url'] = "{}{}".format(
                self.domain, category.xpath('.//a/@href').extract()[0])
            self.logger.info("CATEGORY:TITLE:%s", category_item['title'])
            self.logger.info("CATEGORY:URL:%s", category_item['url'])
            if category_item.get('url'):
                yield scrapy.Request(
                    category_item['url'],
                    meta={
                        'category': category_item
                    },
                    callback=self.parse_category)

    def parse_category(self, response):
        '''
        stage 2.
        parent category
        Will present a list of sub categories to select from
        '''
        self.driver.get(response.url)
        category = response.meta['category']
        response = TextResponse(
            url=response.url, body=self.driver.page_source, encoding='utf-8')
        for sub_cat in response.xpath('//wow-categories-spinner-category-mf'):
            sub_category = Category()
            sub_category['title'] = sub_cat.xpath('.//span/text()').extract()[
                0]
            sub_category['url'] = "{}{}".format(
                self.domain, sub_cat.xpath('.//a/@href').extract()[0])
            self.logger.info("SUB_CATEGORY:TITLE:%s", sub_category['title'])
            self.logger.info("SUB_CATEGORY:URL:%s", sub_category['url'])
            if sub_category.get('url'):
                yield scrapy.Request(
                    sub_category['url'],
                    meta={
                        'url': sub_category['url'],
                        'category': category,
                        'sub_category': sub_category
                    },
                    callback=self.parse_sub_category_pages)

    def parse_sub_category_pages(self, response):
        '''
        stage 3.
        Should be the final (bottom child of the nested category strucutre)
        Will have multiple pages of items, need to iterate through these
        '''
        self.driver.get(response.url)
        category = response.meta['category']
        sub_category = response.meta['sub_category']
        page_url = response.meta['url']
        response = TextResponse(
            url=response.url, body=self.driver.page_source, encoding='utf-8')
        # from scrapy.shell import inspect_response
        # inspect_response(response, self)
        pages = response.xpath(
            '//div[@class="paging _pagingControl"]//a[contains(@class, "page")]'
        )

        for page in pages:
            page_select_url = page.xpath('@href').extract()
            if not page_select_url:
                # must be the active page (no url to select page)
                # TODO: find a way to implement the first-page scrape in a
                # cleaner way to then smoothly transition to the next pages
                page_number = "1"
                page_url = response.url
                self.logger.info("SUB_CATEGORY:PAGE(active):%s", page_number)
                self.logger.info("SUB_CATEGORY:URL:%s", page_url)

                items = response.xpath('//wow-card[@card="card"]')
                self.logger.info("ITEMS_PAGE:CATEGORY:%s", ':' \
                    .join([category['title'],
                    sub_category['title'],
                    sub_sub_category['title']]
                ))
                self.logger.info("ITEMS_PAGE:PAGE_NUMBER:%s", page_number)
                self.logger.info("ITEMS_PAGE:PAGE_URL:%s", response.url)
                self.logger.info("ITEMS_PAGE:ITEM_COUNT:%s", len(items))

                for product in items:
                    item = Item()
                    # TODO: improve logic here, seems messy
                    url = product.xpath(
                        './/a[contains(@class, "InnerDes")]/@href').extract()

                    name = product.xpath(
                        './/div[@class="shelfProductStamp-productName"]//span[1]/text()'
                    ).extract()
                    price_amount = product \
                        .xpath('.//span[@class="pricingContainer-priceAmount"]/text()').extract()
                    price_cup = product \
                        .xpath('.//span[@class="pricingContainer-priceCup"]/text()').extract()
                    size = product \
                        .xpath(
                        './/div[@class="shelfProductStamp-productName"]//span[2]/text()'
                        ).extract()
                    if url:
                        item['url'] = url[0]
                    if name:
                        item['name'] = name[0].strip()
                    if price_amount:
                        item['price_amount'] = price_amount[0].strip()
                    if price_cup:
                        item['price_cup'] = price_cup[0].strip()
                    if size:
                        item['size'] = size[0].strip()
                    item['category'] = category['title'].strip()
                    item['sub_category'] = sub_category['title'].strip()
                    yield item

            else:

                next_page_url = "{}{}".format(page_url, page_select_url[0])
                page_number = re.search('.*\=([0-9]+)',
                                        next_page_url).groups()[0]
                self.logger.info("SUB_CATEGORY:PAGE:%s", page_number)
                self.logger.info("SUB_CATEGORY:URL:%s", next_page_url)
                yield scrapy.Request(
                    next_page_url,
                    meta={
                        'page': page_number,
                        'category': category,
                        'sub_category': sub_category,
                    },
                    callback=self.parse_items_page)

    def parse_items_page(self, response):
        '''
        Parse item lists and collect links. Used only on subsequent pages
        not the default active first page
        '''
        page_number = response.meta['page'] if response.meta.get(
            'page') else "1"
        category = response.meta['category']
        sub_category = response.meta['sub_category']

        self.driver.get(response.url)
        response = TextResponse(
            url=response.url, body=self.driver.page_source, encoding='utf-8')

        items = response.xpath('//wow-card[@card="card"]')
        self.logger.info("ITEMS_PAGE:CATEGORY:%s", ':' \
            .join([category['title'],
            sub_category['title']]
        ))
        self.logger.info("ITEMS_PAGE:PAGE_NUMBER:%s", page_number)
        self.logger.info("ITEMS_PAGE:PAGE_URL:%s", response.url)
        self.logger.info("ITEMS_PAGE:ITEM_COUNT:%s", len(items))

        for product in items:
            item = Item()
            # TODO: improve logic here, seems messy
            url = product.xpath(
                './/a[contains(@class, "InnerDes")]/@href').extract()

            name = product.xpath(
                './/div[@class="shelfProductStamp-productName"]//span[1]/text()'
            ).extract()
            price_amount = product \
                .xpath('.//span[@class="pricingContainer-priceAmount"]/text()').extract()
            price_cup = product \
                .xpath('.//span[@class="pricingContainer-priceCup"]/text()').extract()
            size = product \
                .xpath(
                './/div[@class="shelfProductStamp-productName"]//span[2]/text()'
                ).extract()
            if url:
                item['url'] = url[0]
            if name:
                item['name'] = name[0].strip()
            if price_amount:
                item['price_amount'] = price_amount[0].strip()
            if price_cup:
                item['price_cup'] = price_cup[0].strip()
            if size:
                item['size'] = size[0].strip()
            item['category'] = category['title'].strip()
            item['sub_category'] = sub_category['title'].strip()
            yield item
