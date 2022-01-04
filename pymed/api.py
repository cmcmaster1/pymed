import datetime
import requests
import itertools

import lxml.etree as xml

from typing import Union

# from .helpers import batches
from .article import PubMedArticle
from .book import PubMedBookArticle


# Base url for all queries
BASE_URL = "https://eutils.ncbi.nlm.nih.gov"


class PubMed(object):
    """Wrapper around the PubMed API."""

    def __init__(
        self: object,
        tool: str = "my_tool",
        email: str = "my_email@example.com",
        api_key: str = None,
    ) -> None:
        """Initialization of the object.

        Parameters:
            - tool      String, name of the tool that is executing the query.
                        This parameter is not required but kindly requested by
                        PMC (PubMed Central).
            - email     String, email of the user of the tool. This parameter
                        is not required but kindly requested by PMC (PubMed Central).
            - api_key   String, API key for the tool. This parameter is not required

        Returns:
            - None
        """

        # Store the input parameters
        self.tool = tool
        self.email = email
        self.api_key = api_key

        # Keep track of the rate limit
        self._rateLimit = 3
        self._requestsMade = []

        # Define the standard / default query parameters
        self.parameters = {"tool": tool, "email": email, "db": "pubmed"}

        # Add the API key if it is provided
        if api_key:
            self.parameters["api_key"] = api_key

    def split_range(self: object, max: int = 20_000):
        """Helper method to split a range of numbers into batches of a maximum size 10_000."""
        for i in range(0, max, 10_000):
            yield i, min(i + 10_000, max)

    def query(self: object, query: str, max_results: int = 100):
        """Method that executes a query against the GraphQL schema, automatically
        inserting the PubMed data loader.

        Parameters:
            - query     String, the GraphQL query to execute against the schema.
            - max_results    Int, maximum number of results to return.
            - batch_size     Int, number of articles to retrieve in each batch.

        Returns:
            - result    ExecutionResult, GraphQL object that contains the result
                        in the "data" attribute.
        """

        # Retrieve the PubMed query data
        self.query_data = self._getPubMedData(query=query, max_results=max_results)

        # Get the articles (split into batches of 10_000 if max_results is greater than 10_000)
        if max_results > 10_000:
            self.articles = list(
                itertools.chain.from_iterable(
                    self._getArticlesEnv(
                        query_data=self.query_data, start=start, end=end
                    )
                    for start, end in self.split_range(max=max_results)
                )
            )
        else:
            self.articles = self._getArticlesEnv(
                query_data=self.query_data, start=0, end=max_results
            )

        #### Old method - archived for reference
        # Retrieve the article IDs for the query
        # article_ids = self._getArticleIds(query=query, max_results=max_results)

        # Get the articles themselves
        # articles = [
        #     self._getArticles(article_ids=batch)
        #     for batch in batches(article_ids, batch_size)
        # ]

        # Chain the batches back together and return the list
        return self.articles

    def getTotalResultsCount(self: object, query: str) -> int:
        """Helper method that returns the total number of results that match the query.

        Parameters:
            - query                 String, the query to send to PubMed

        Returns:
            - total_results_count   Int, total number of results for the query in PubMed
        """

        # Get the default parameters
        parameters = self.parameters.copy()

        # Add specific query parameters
        parameters["term"] = query
        parameters["retmax"] = 1

        # Make the request (request a single article ID for this search)
        response = self._get(url="/entrez/eutils/esearch.fcgi", parameters=parameters)

        # Return the total number of results (without retrieving them)
        return int(response.get("esearchresult", {}).get("count"))

    def _exceededRateLimit(self) -> bool:
        """Helper method to check if we've exceeded the rate limit.

        Returns:
            - exceeded      Bool, Whether or not the rate limit is exceeded.
        """

        # Remove requests from the list that are longer than 1 second ago
        self._requestsMade = [
            requestTime
            for requestTime in self._requestsMade
            if requestTime > datetime.datetime.now() - datetime.timedelta(seconds=1)
        ]

        # Return whether we've made more requests in the last second, than the rate limit
        return len(self._requestsMade) > self._rateLimit

    def _get(
        self: object, url: str, parameters: dict, output: str = "json"
    ) -> Union[dict, str]:
        """Generic helper method that makes a request to PubMed.

        Parameters:
            - url           Str, last part of the URL that is requested (will
                            be combined with the base url)
            - parameters    Dict, parameters to use for the request
            - output        Str, type of output that is requested (defaults to
                            JSON but can be used to retrieve XML)

        Returns:
            - response      Dict / str, if the response is valid JSON it will
                            be parsed before returning, otherwise a string is
                            returend
        """

        # Make sure the rate limit is not exceeded
        while self._exceededRateLimit():
            pass

        # Set the response mode
        parameters["retmode"] = output

        # Make the request to PubMed
        response = requests.get(f"{BASE_URL}{url}", params=parameters)

        # Check for any errors
        response.raise_for_status()

        # Add this request to the list of requests made
        self._requestsMade.append(datetime.datetime.now())

        # Return the response
        if output == "json":
            return response.json()
        else:
            return response.text

    def _getPubMedData(self: object, query: str, max_results: int) -> dict:
        """Helper method to retrieve the QueryKey and WebEnv for a query.

        Parameters:
            - query         String, the query to send to PubMed
            - max_results   Int, maximum number of results to return

        Returns:
            - data          List, PubMed data.
        """

        # Get the default parameters
        parameters = self.parameters.copy()

        # Add specific query parameters
        parameters["term"] = query
        parameters["usehistory"] = "y"
        parameters["retmax"] = max_results

        # Make the request
        response = self._get(
            url="/entrez/eutils/esearch.fcgi", parameters=parameters, output="xml"
        ).encode("utf-8")
        root = xml.fromstring(response)
        query_key = root.xpath(".//QueryKey")[0].text
        WebEnv = root.xpath(".//WebEnv")[0].text

        # Return the data
        return {"query_key": query_key, "WebEnv": WebEnv}

    def _getArticlesEnv(
        self: object, query_data: list, start: int = 0, end: int = 100
    ) -> list:
        """Helper method that batches a list of article IDs and retrieves the content.

        Parameters:
            - query_data    List, PubMed data.

        Returns:
            - articles      List, article objects.
        """

        # Get the default parameters
        parameters = self.parameters.copy()
        parameters["query_key"] = query_data["query_key"]
        parameters["WebEnv"] = query_data["WebEnv"]
        parameters["retmax"] = end
        parameters["retstart"] = start

        # Make the request
        response = self._get(
            url="/entrez/eutils/efetch.fcgi", parameters=parameters, output="xml"
        )

        # Parse as XML
        root = xml.fromstring(response)

        # Loop over the articles and construct article objects
        for article in root.iter("PubmedArticle"):
            yield PubMedArticle(xml_element=article)
        for book in root.iter("PubmedBookArticle"):
            yield PubMedBookArticle(xml_element=book)

    #### The following methods are archived for reference ####
    def _getArticles(self: object, article_ids: list) -> list:
        """Helper method that batches a list of article IDs and retrieves the content.

        Parameters:
            - article_ids   List, article IDs.

        Returns:
            - articles      List, article objects.
        """

        # Get the default parameters
        parameters = self.parameters.copy()
        parameters["id"] = article_ids

        # Make the request
        response = self._get(
            url="/entrez/eutils/efetch.fcgi", parameters=parameters, output="xml"
        )

        # Parse as XML
        root = xml.fromstring(response)

        # Loop over the articles and construct article objects
        for article in root.iter("PubmedArticle"):
            yield PubMedArticle(xml_element=article)
        for book in root.iter("PubmedBookArticle"):
            yield PubMedBookArticle(xml_element=book)

    def _getArticleIds(self: object, query: str, max_results: int) -> list:
        """Helper method to retrieve the article IDs for a query.

        Parameters:
            - query         Str, query to be executed against the PubMed database.
            - max_results   Int, the maximum number of results to retrieve.

        Returns:
            - article_ids   List, article IDs as a list.
        """

        # Create a placeholder for the retrieved IDs
        article_ids = []

        # Get the default parameters
        parameters = self.parameters.copy()

        # Add specific query parameters
        parameters["term"] = query
        parameters["retmax"] = 50000

        # Calculate a cut off point based on the max_results parameter
        if max_results < parameters["retmax"]:
            parameters["retmax"] = max_results

        # Make the first request to PubMed
        response = self._get(url="/entrez/eutils/esearch.fcgi", parameters=parameters)

        # Add the retrieved IDs to the list
        article_ids += response.get("esearchresult", {}).get("idlist", [])

        # Get information from the response
        total_result_count = int(response.get("esearchresult", {}).get("count"))
        retrieved_count = int(response.get("esearchresult", {}).get("retmax"))

        # If no max is provided (-1) we'll try to retrieve everything
        if max_results == -1:
            max_results = total_result_count

        # If not all articles are retrieved, continue to make requests untill we have everything
        while retrieved_count < total_result_count and retrieved_count < max_results:

            # Calculate a cut off point based on the max_results parameter
            if (max_results - retrieved_count) < parameters["retmax"]:
                parameters["retmax"] = max_results - retrieved_count

            # Start the collection from the number of already retrieved articles
            parameters["retstart"] = retrieved_count

            # Make a new request
            response = self._get(
                url="/entrez/eutils/esearch.fcgi", parameters=parameters
            )

            # Add the retrieved IDs to the list
            article_ids += response.get("esearchresult", {}).get("idlist", [])

            # Get information from the response
            retrieved_count += int(response.get("esearchresult", {}).get("retmax"))

        # Return the response
        return article_ids
