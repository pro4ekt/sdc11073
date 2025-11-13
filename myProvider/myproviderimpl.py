import logging
from enum import Enum
from sdc11073.provider import SdcProvider
from sdc11073.pysoap.soapenvelope import ReceivedSoapMessage
from sdc11073.provider.operations import OperationDefinitionBase
from sdc11073.xml_types.msg_types import AbstractSet

class MySdcProvider(SdcProvider):

    requests = []

    def find_string_in_request(self, request: ReceivedSoapMessage, text_to_find: str) -> bool:
        """
        Ищет строку text_to_find в сырых данных (raw_data) запроса.

        :param request: Объект ReceivedSoapMessage.
        :param text_to_find: Строка для поиска.
        :return: True, если строка найдена, иначе False.
        """
        if not hasattr(request, 'raw_data') or not request.raw_data:
            #self._logger.warning('Request object has no raw_data attribute or it is empty.')
            return False

        try:
            # request.raw_data - это байты, поэтому искомую строку тоже кодируем в байты
            search_bytes = text_to_find.encode('utf-8')
            return search_bytes in request.raw_data
        except Exception as e:
            #self._logger.error('Error while searching in request raw_data: %s', e)
            return False

    def handle_operation_request(self,
                                 operation: OperationDefinitionBase,
                                 request: ReceivedSoapMessage,
                                 operation_request: AbstractSet,
                                 transaction_id: int) -> Enum:
        """
        Перехватывает вызов, логирует информацию об операции и передает дальше.
        """
        self.requests.append(request)
        #self._logger.info('Received operation request for handle: %s', operation.handle)
        # Вызываем оригинальный метод из родительского класса SdcProvider
        return super().handle_operation_request(operation, request, operation_request, transaction_id)