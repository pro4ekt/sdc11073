import logging
from enum import Enum
from sdc11073.provider import SdcProvider
from sdc11073.pysoap.soapenvelope import ReceivedSoapMessage
from sdc11073.provider.operations import OperationDefinitionBase
from sdc11073.xml_types.msg_types import AbstractSet

class MySdcProvider(SdcProvider):

    requests = []

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